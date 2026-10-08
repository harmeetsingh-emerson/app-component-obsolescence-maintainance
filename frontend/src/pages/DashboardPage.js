import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import {
  Alert, Box, Button, Chip, Divider, FormControl, IconButton, LinearProgress,
  MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Tooltip, Typography
} from "@mui/material";
import {
  ArrowBack, Assignment, Autorenew, CheckCircle, Help, Memory, ReceiptLong,
  Settings, ShieldOutlined, Sms, TableChart, ViewModule, Warning
} from "@mui/icons-material";

const normalizeLifecycleStatus = (eolValue, yeolValue) => {
  const lifecycle = String(eolValue || "").trim().toLowerCase();
  const yeol = Number.parseFloat(yeolValue);

  if (lifecycle.includes("pcn")) return "PCN";
  if (lifecycle.includes("nrnd") || lifecycle.includes("not recommended")) return "NRND";
  if (
    lifecycle.includes("eol") ||
    lifecycle.includes("obsolete") ||
    lifecycle.includes("discontinued") ||
    lifecycle.includes("not found") ||
    (!Number.isNaN(yeol) && yeol < 5)
  ) return "EOL";
  if (lifecycle === "no" || lifecycle.includes("active") || lifecycle.includes("production")) return "Active";
  return "Unknown";
};

const getDashboardMetrics = (rows) => {
  const lifecycleCounts = { Active: 0, NRND: 0, EOL: 0, PCN: 0, Unknown: 0 };

  rows.forEach(row => {
    const status = normalizeLifecycleStatus(row.EOL, row.YEOL);
    lifecycleCounts[status] = (lifecycleCounts[status] || 0) + 1;
  });

  const atRisk = rows.filter(row => {
    const status = normalizeLifecycleStatus(row.EOL, row.YEOL);
    return ["EOL", "NRND", "PCN", "Unknown"].includes(status);
  }).length;

  const score = rows.length ? Math.min(99, Math.round((atRisk / rows.length) * 100)) : 0;

  return {
    totalParts: rows.length,
    atRisk,
    score,
    lifecycleCounts,
    highRisk: lifecycleCounts.EOL + lifecycleCounts.Unknown,
    eolNrnd: lifecycleCounts.EOL + lifecycleCounts.NRND,
    pendingPcn: lifecycleCounts.PCN,
  };
};

const statusColor = (status) => {
  if (status === "EOL") return { bg: "#c5301c", color: "#fff" };
  if (status === "NRND") return { bg: "#5a8f1b", color: "#fff" };
  if (status === "PCN") return { bg: "#c5301c", color: "#fff" };
  if (status === "Active") return { bg: "#ef9709", color: "#fff" };
  return { bg: "#667085", color: "#fff" };
};
const getRiskLevel = (score) => {
  if (score >= 67) return { label: "High", color: "#d7191c" };
  if (score >= 34) return { label: "Moderate", color: "#ef9709" };
  return { label: "Low", color: "#1ca427" };
};

const getPartNumber = (row) => row["Parent Part Number"] || row["Requested Part"] || row["Manufacturer Part Number"] || "-";

const getRiskReason = (row) => {
  const status = normalizeLifecycleStatus(row.EOL, row.YEOL);
  const yeol = Number.parseFloat(row.YEOL);

  if (status === "EOL") {
    return Number.isNaN(yeol) ? "EOL/obsolete or unresolved lifecycle" : `YEOL ${row.YEOL} is below 5 years`;
  }
  if (status === "NRND") return "Lifecycle indicates NRND/not recommended";
  if (status === "PCN") return "Lifecycle indicates active PCN notice";
  if (status === "Active") return Number.isNaN(yeol) ? "Lifecycle marked active" : `Active lifecycle with ${row.YEOL} years to EOL`;
  return "Lifecycle data was not returned or could not be classified";
};

const formatDate = () => new Date().toLocaleDateString("en-US", {
  month: "2-digit",
  day: "2-digit",
  year: "numeric",
});

function readStoredRows() {
  try {
    return JSON.parse(localStorage.getItem("latestDashboardRows") || "[]");
  } catch (_) {
    return [];
  }
}

function DashboardPage({ apiBase, files, initialRows, selectedFilename, onRowsChange }) {
  const location = useLocation();
  const routeRows = Array.isArray(location.state?.rows) ? location.state.rows : [];
  const storedRows = useMemo(() => readStoredRows(), []);
  const [dashboardFile, setDashboardFile] = useState(location.state?.filename || selectedFilename || "");
  const [rows, setRows] = useState(routeRows.length ? routeRows : initialRows?.length ? initialRows : storedRows);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!dashboardFile && files.length > 0) {
      setDashboardFile(files[0].filename);
    }
  }, [dashboardFile, files]);

  const updateRows = useCallback((nextRows) => {
    setRows(nextRows);
    onRowsChange?.(nextRows);
    if (nextRows.length) {
      localStorage.setItem("latestDashboardRows", JSON.stringify(nextRows));
    } else {
      localStorage.removeItem("latestDashboardRows");
    }
  }, [onRowsChange]);

  const fetchDashboardData = useCallback(async (filename) => {
    if (!filename) {
      updateRows([]);
      return;
    }

    setLoading(true);
    setError("");

    try {
      const response = await fetch(`${apiBase}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: "get me details of part numbers",
          filename,
        }),
      });
      const result = await response.json();

      if (!response.ok || result.success === false) {
        throw new Error(result.message || "Unable to load dashboard data");
      }

      updateRows(Array.isArray(result.excel_data) ? result.excel_data : []);
    } catch (err) {
      updateRows([]);
      setError(err.message || "Unable to load dashboard data");
    } finally {
      setLoading(false);
    }
  }, [apiBase, updateRows]);

  const metrics = getDashboardMetrics(rows);
  const riskLevel = getRiskLevel(metrics.score);
  const maxLifecycleCount = Math.max(...Object.values(metrics.lifecycleCounts), 1);
  const lifecycleChartData = [
    { status: "Active", count: metrics.lifecycleCounts.Active, color: "#3d66a0" },
    { status: "NRND", count: metrics.lifecycleCounts.NRND, color: "#78b813" },
    { status: "EOL", count: metrics.lifecycleCounts.EOL, color: "#ef3124" },
    { status: "PCN", count: metrics.lifecycleCounts.PCN, color: "#ffb000" },
  ];
  const tableRows = [...rows].sort((left, right) => {
    const priority = { EOL: 0, Unknown: 1, NRND: 2, PCN: 3, Active: 4 };
    return priority[normalizeLifecycleStatus(left.EOL, left.YEOL)] - priority[normalizeLifecycleStatus(right.EOL, right.YEOL)];
  });

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#1e1f21", p: { xs: 1.5, md: 3 } }}>
      <Box sx={{ width: "100%", maxWidth: 1680, mx: "auto", bgcolor: "#edf1f6", minHeight: "calc(100vh - 48px)", boxShadow: "0 22px 60px rgba(0,0,0,0.35)" }}>
        <Box sx={{
          minHeight: 64, px: { xs: 2, md: 4 }, py: 1.25, display: "flex", alignItems: "center", justifyContent: "space-between",
          background: "linear-gradient(90deg, #07386f 0%, #0b66a5 100%)", color: "#fff",
          boxShadow: "0 2px 8px rgba(0,0,0,0.22)", gap: 2,
        }}>
          <Button component={RouterLink} to="/" startIcon={<ArrowBack />} sx={{ color: "#fff", textTransform: "none", fontWeight: 800 }}>
            Query
          </Button>
          <Typography variant="h5" sx={{ fontWeight: 900, letterSpacing: 0.2, textAlign: "center", fontFamily: "Georgia, 'Times New Roman', serif" }}>
            Obsolescence Management Dashboard
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ display: { xs: "none", sm: "flex" } }}>
            <ShieldOutlined />
            <Help />
          </Stack>
        </Box>

        <Box sx={{ p: { xs: 2, md: 3 }, display: "grid", gridTemplateColumns: "1fr", gap: 2 }}>
          <Paper elevation={2} sx={{ borderRadius: 1, p: 2.3, border: "1px solid #c7d2e2", bgcolor: "#f8fafc" }}>
            <Typography variant="h6" sx={{ fontWeight: 900, color: "#18345a", pb: 1, borderBottom: "2px solid #c8d2df", fontFamily: "Georgia, 'Times New Roman', serif" }}>
              Obsolescence Risk Overview
            </Typography>

            <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" }, mt: 2, mb: 2, borderBottom: "2px solid #c8d2df", pb: 2 }}>
              {[
                { icon: <Warning sx={{ fontSize: 46, color: "#e1261c" }} />, label: "High Risk Parts:", value: metrics.highRisk, color: "#e1261c" },
                { icon: <ReceiptLong sx={{ fontSize: 44, color: "#f28b00" }} />, label: "EOL / NRND Components:", value: metrics.eolNrnd, color: "#18345a" },
                { icon: <Assignment sx={{ fontSize: 44, color: "#ffc400" }} />, label: "Pending PCNs:", value: metrics.pendingPcn, color: "#18345a" },
              ].map((item, index) => (
                <Box key={item.label} sx={{ display: "flex", alignItems: "center", gap: 1.2, px: { xs: 0, sm: 1.5 }, py: { xs: 1, sm: 0 }, borderLeft: index === 0 ? "none" : { xs: "none", sm: "2px solid #d2dbe7" } }}>
                  {item.icon}
                  <Box>
                    <Typography sx={{ fontWeight: 900, color: "#18345a", lineHeight: 1.05 }}>{item.label}</Typography>
                    <Typography component="span" sx={{ fontSize: 28, fontWeight: 900, color: item.color, lineHeight: 1 }}>{item.value}</Typography>
                  </Box>
                </Box>
              ))}
            </Box>

            <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "minmax(360px, 0.9fr) minmax(520px, 1.4fr)" }, gap: 3, alignItems: "end" }}>
              <Box>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
                  <Typography variant="h6" sx={{ fontWeight: 900, color: "#17345b", fontFamily: "Georgia, 'Times New Roman', serif" }}>
                    Overall Risk Score
                  </Typography>
                  <Help sx={{ color: "#17345b" }} />
                </Stack>
                <Box sx={{ border: "1px solid #c8d2df", bgcolor: "#fff", borderRadius: 2, p: 2.2, boxShadow: "0 12px 28px rgba(23,52,91,0.08)", overflow: "hidden" }}>
                  <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
                    <Box>
                      <Typography sx={{ color: "#526786", fontSize: 13, fontWeight: 900, textTransform: "uppercase", letterSpacing: 0.4 }}>
                        At-risk ratio
                      </Typography>
                      <Typography sx={{ color: "#17345b", fontSize: { xs: 42, md: 54 }, fontWeight: 900, lineHeight: 0.95 }}>
                        {metrics.score}%
                      </Typography>
                    </Box>
                    <Box sx={{ textAlign: "right" }}>
                      <Chip
                        label={`${riskLevel.label} Risk`}
                        sx={{ bgcolor: riskLevel.color, color: "#fff", fontWeight: 900, fontSize: 15, height: 36, px: 0.8, borderRadius: 999 }}
                      />
                      <Typography sx={{ mt: 0.7, color: "#526786", fontSize: 12, fontWeight: 800 }}>
                        {metrics.atRisk} of {metrics.totalParts} rows flagged
                      </Typography>
                    </Box>
                  </Stack>

                  <Box sx={{ position: "relative", pt: 2.6, pb: 2.2 }}>
                    <Box sx={{ height: 16, borderRadius: 999, overflow: "hidden", display: "grid", gridTemplateColumns: "34fr 33fr 33fr", boxShadow: "inset 0 0 0 1px rgba(23,52,91,0.14)" }}>
                      <Box sx={{ bgcolor: "#19a63a" }} />
                      <Box sx={{ bgcolor: "#f39a10" }} />
                      <Box sx={{ bgcolor: "#dc1f26" }} />
                    </Box>
                    <Box sx={{ position: "absolute", top: 0, left: `calc(${Math.min(100, Math.max(0, metrics.score))}% - 18px)`, width: 36, height: 24, borderRadius: 1, bgcolor: "#17345b", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 900, boxShadow: "0 5px 14px rgba(23,52,91,0.28)", "&::after": { content: '""', position: "absolute", left: "50%", bottom: -5, transform: "translateX(-50%)", width: 0, height: 0, borderLeft: "5px solid transparent", borderRight: "5px solid transparent", borderTop: "5px solid #17345b" } }}>
                      {metrics.score}%
                    </Box>
                  </Box>

                  <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5, px: 0.2 }}>
                    <Typography sx={{ color: "#167a31", fontSize: 12, fontWeight: 900 }}>Low 0-33%</Typography>
                    <Typography sx={{ color: "#b96500", fontSize: 12, fontWeight: 900 }}>Moderate 34-66%</Typography>
                    <Typography sx={{ color: "#b71920", fontSize: 12, fontWeight: 900 }}>High 67-100%</Typography>
                  </Stack>

                  <Box sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1.2, mt: 2.2 }}>
                    <Box sx={{ bgcolor: "#edf1f6", border: "1px solid #d7e0ec", borderRadius: 1.5, p: 1.3 }}>
                      <Typography sx={{ fontSize: 12, color: "#526786", fontWeight: 800 }}>At-risk rows</Typography>
                      <Typography sx={{ color: "#17345b", fontSize: 24, fontWeight: 900 }}>{metrics.atRisk}</Typography>
                    </Box>
                    <Box sx={{ bgcolor: "#edf1f6", border: "1px solid #d7e0ec", borderRadius: 1.5, p: 1.3 }}>
                      <Typography sx={{ fontSize: 12, color: "#526786", fontWeight: 800 }}>Analyzed rows</Typography>
                      <Typography sx={{ color: "#17345b", fontSize: 24, fontWeight: 900 }}>{metrics.totalParts}</Typography>
                    </Box>
                  </Box>
                </Box>
                <Typography sx={{ mt: 1.2, fontSize: 13, lineHeight: 1.45, textAlign: "left", fontWeight: 800, color: "#17345b" }}>
                  Risk score = at-risk rows divided by all analyzed rows. EOL, NRND, PCN, unknown lifecycle, and YEOL below 5 years are counted as at-risk.
                </Typography>
              </Box>

              <Box>
                <Typography variant="h6" sx={{ fontWeight: 900, color: "#17345b", pb: 1, mb: 2, borderBottom: "2px solid #c8d2df", fontFamily: "Georgia, 'Times New Roman', serif" }}>
                  Lifecycle Status Distribution
                </Typography>
                <Box sx={{ height: 260, bgcolor: "#fff", border: "1px solid #c8d2df", borderRadius: 1, p: 1.5 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={lifecycleChartData} margin={{ top: 24, right: 18, left: 0, bottom: 12 }} barCategoryGap="34%">
                      <CartesianGrid stroke="#d8e0ea" vertical={false} strokeDasharray="4 4" />
                      <XAxis dataKey="status" axisLine={{ stroke: "#7d92b0", strokeWidth: 2 }} tickLine={false} tick={{ fill: "#17345b", fontSize: 13, fontWeight: 800 }} dy={8} />
                      <YAxis allowDecimals={false} domain={[0, Math.max(3, maxLifecycleCount)]} axisLine={false} tickLine={false} tick={{ fill: "#526786", fontSize: 12, fontWeight: 700 }} />
                      <ChartTooltip
                        cursor={{ fill: "rgba(82,103,134,0.08)" }}
                        contentStyle={{ borderRadius: 8, border: "1px solid #c8d2df", boxShadow: "0 8px 22px rgba(23,52,91,0.12)" }}
                        formatter={(value) => [`${value} components`, "Count"]}
                        labelStyle={{ color: "#17345b", fontWeight: 900 }}
                      />
                      <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={110}>
                        <LabelList dataKey="count" position="top" fill="#17345b" fontSize={14} fontWeight={900} />
                        {lifecycleChartData.map(item => <Cell key={item.status} fill={item.color} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </Box>
              </Box>
            </Box>
          </Paper>

          <Paper elevation={2} sx={{ borderRadius: 1, overflow: "hidden", border: "1px solid #c7d2e2", bgcolor: "#f8fafc" }}>
            <Box sx={{ p: 2.3, pb: 1.4 }}>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "stretch", sm: "center" }} justifyContent="space-between" sx={{ pb: 1, borderBottom: "2px solid #c8d2df" }}>
                <Typography variant="h6" sx={{ fontWeight: 900, color: "#18345a", fontFamily: "Georgia, 'Times New Roman', serif" }}>
                  Bill of Materials Status
                </Typography>
                <Stack direction="row" spacing={1} alignItems="center">
                  <FormControl size="small" sx={{ minWidth: 190, bgcolor: "#fff" }}>
                    <Select
                      value={dashboardFile}
                      onChange={event => {
                        const nextFile = event.target.value;
                        setDashboardFile(nextFile);
                        fetchDashboardData(nextFile);
                      }}
                      displayEmpty
                    >
                      {files.length === 0 && <MenuItem value="">No indexed BOMs</MenuItem>}
                      {files.map(item => <MenuItem key={item.filename} value={item.filename}>{item.filename}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <Tooltip title="Refresh dashboard">
                    <IconButton onClick={() => fetchDashboardData(dashboardFile)} disabled={!dashboardFile || loading} aria-label="refresh dashboard">
                      <Autorenew />
                    </IconButton>
                  </Tooltip>
                  <Settings sx={{ color: "#244d7d" }} />
                </Stack>
              </Stack>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={2.5} alignItems={{ xs: "flex-start", sm: "center" }} sx={{ py: 1.5 }}>
                <Typography sx={{ fontWeight: 900, color: "#17345b" }}>Total Parts: {metrics.totalParts}</Typography>
                <Divider orientation="vertical" flexItem sx={{ display: { xs: "none", sm: "block" } }} />
                <Typography sx={{ fontWeight: 900, color: "#17345b" }}>At Risk: {metrics.atRisk}</Typography>
                <CheckCircle sx={{ color: metrics.atRisk ? "#bd2217" : "#238636" }} />
                <Divider orientation="vertical" flexItem sx={{ display: { xs: "none", sm: "block" } }} />
                <Typography sx={{ fontWeight: 900, color: "#17345b" }}>Last Updated: {formatDate()}</Typography>
              </Stack>
            </Box>

            {loading && <LinearProgress />}
            {error && <Alert severity="warning" sx={{ mx: 2.3, mb: 1.5 }}>{error}</Alert>}
            {rows.length > 0 && !loading && !error && (
              <Alert severity="info" sx={{ mx: 2.3, mb: 1.5 }}>
                Analysis is based on all {rows.length} returned component/manufacturer rows. Risk is derived from lifecycle status and YEOL: EOL, NRND, PCN, unknown, or YEOL below 5 years are counted as at-risk.
              </Alert>
            )}
            {!rows.length && !loading && !error && (
              <Alert severity="info" sx={{ mx: 2.3, mb: 1.5 }}>
                Run a query from the landing page, then open this dashboard to see the matching results.
              </Alert>
            )}

            <TableContainer sx={{ px: 0, maxHeight: 540, width: "100%" }}>
              <Table size="small" sx={{ tableLayout: "fixed", minWidth: 1280, width: "100%" }}>
                <TableHead>
                  <TableRow sx={{ bgcolor: "#526786" }}>
                    {["Part Number", "Manufacturer Part", "Manufacturer", "Description", "Status", "YEOL","RoHS", "Reason"].map(header => (
                      <TableCell key={header} sx={{ color: "#fff", fontWeight: 900, fontSize: 15, borderColor: "#8fa0b8", position: "sticky", top: 0, zIndex: 1, bgcolor: "#526786" }}>{header}</TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {tableRows.map((row, index) => {
                    const status = normalizeLifecycleStatus(row.EOL, row.YEOL);
                    const chip = statusColor(status);
                    return (
                      <TableRow key={`${getPartNumber(row)}-${row["Manufacturer Part Number"] || "mpn"}-${index}`} sx={{ bgcolor: index % 2 ? "#f4f6f9" : "#fff" }}>
                        <TableCell sx={{ fontWeight: 900 }}>{getPartNumber(row)}</TableCell>
                        <TableCell sx={{ fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row["Manufacturer Part Number"] || "-"}</TableCell>
                        <TableCell sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row["Manufacturer Name"] || "-"}</TableCell>
                        <TableCell sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.Description || row.PlName || "-"}</TableCell>
                        <TableCell>
                          <Chip label={status} size="small" sx={{ bgcolor: chip.bg, color: chip.color, fontWeight: 900, minWidth: 68 }} />
                        </TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>{row.YEOL || "-"}</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>{row.RoHS || "-"}</TableCell>
                        <TableCell sx={{ fontSize: 12, color: "#334155" }}>{getRiskReason(row)}</TableCell>
                      </TableRow>
                    );
                  })}
                  {!loading && tableRows.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={8} sx={{ py: 4, textAlign: "center", color: "text.secondary" }}>  No dashboard rows available yet.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>

            <Box sx={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", bgcolor: "#d9e2ef", borderTop: "1px solid #b8c6d8", mt: 2 }}>
              {[TableChart, ViewModule, Memory, Sms].map((Icon, index) => (
                <Box key={index} sx={{ display: "flex", justifyContent: "center", py: 1, borderLeft: index ? "2px solid #b8c6d8" : "none" }}>
                  <Icon sx={{ color: "#244d7d", fontSize: 28 }} />
                </Box>
              ))}
            </Box>
          </Paper>
        </Box>
      </Box>
    </Box>
  );
}

export default DashboardPage;
