import React, { useState, useRef, useEffect, useCallback } from "react";
import * as XLSX from 'xlsx';
import { BrowserRouter, Link as RouterLink, Route, Routes } from "react-router-dom";
import DashboardPage from "./pages/DashboardPage";
import {
  Button, Typography, LinearProgress, Paper, TextField,
  Alert, Snackbar, Chip, CircularProgress, Divider, Container, Stack,
  Select, MenuItem, FormControl, InputLabel, Tooltip, IconButton, Box,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow
} from "@mui/material";
import {
  CloudUpload, Search, Download, Autorenew, InfoOutlined, Warning,
  Assignment, ReceiptLong, Settings, Help, ShieldOutlined,
  SmartToyOutlined, CheckCircle, TableChart, Dashboard, ViewModule,
  Memory, Sms
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

const formatDate = () => new Date().toLocaleDateString("en-US", {
  month: "2-digit",
  day: "2-digit",
  year: "numeric",
});

// Uses package.json proxy in development unless REACT_APP_API_URL is provided.
const API_BASE = process.env.REACT_APP_API_URL || "";

function App() {
  const [file, setFile] = useState(null);
  const [uploadStatus, setUploadStatus] = useState("");
  const [uploadProgress, setUploadProgress] = useState(0);
  const [ocrPolling, setOcrPolling] = useState(false);       // true while OCR in progress
  const ocrFilenameRef = useRef(null);                       // filename being tracked (ref, not state)
  const [ocrPageInfo, setOcrPageInfo] = useState(null);      // { page, total_pages }
  const ocrPollRef = useRef(null);
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState(""); // Only store the latest answer
  const [excelData, setExcelData] = useState(null); // Excel-ready data
  const [loading, setLoading] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [reindexStatus, setReindexStatus] = useState("");
  const [ocrDpi, setOcrDpi] = useState(200);
  const [uploadBanner, setUploadBanner] = useState(null); // { severity, message } | null
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState("");
  const [activeView, setActiveView] = useState("workspace");
  const [dashboardFile, setDashboardFile] = useState("");
  const [dashboardRows, setDashboardRows] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("latestDashboardRows") || "[]");
    } catch (_) {
      return [];
    }
  });
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState("");
  const answerRef = useRef(null);

  // Fetch list of uploaded BOM files from the backend
  const fetchFiles = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/files`);
      const data = await res.json();
      if (data.files) setFiles(data.files);
    } catch (_) {
      // silently ignore — server may not be up yet
    }
  }, []);

  // Load file list on mount
  useEffect(() => { fetchFiles(); }, [fetchFiles]);

  useEffect(() => {
    if (!dashboardFile && files.length > 0) {
      setDashboardFile(files[0].filename);
    }
  }, [dashboardFile, files]);

  const fetchDashboardData = useCallback(async (filename) => {
    if (!filename) {
      setDashboardRows([]);
      return;
    }

    setDashboardLoading(true);
    setDashboardError("");

    try {
      const response = await fetch(`${API_BASE}/query`, {
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

      setDashboardRows(Array.isArray(result.excel_data) ? result.excel_data : []);
    } catch (err) {
      setDashboardRows([]);
      setDashboardError(err.message || "Unable to load dashboard data");
    } finally {
      setDashboardLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeView === "dashboard" && dashboardFile) {
      fetchDashboardData(dashboardFile);
    }
  }, [activeView, dashboardFile, fetchDashboardData]);

  // ── OCR status polling ────────────────────────────────────────────────────
  const stopOcrPoll = useCallback(() => {
    if (ocrPollRef.current) {
      clearInterval(ocrPollRef.current);
      ocrPollRef.current = null;
    }
    setOcrPolling(false);
    setOcrPageInfo(null);
  }, []);

  const startOcrPoll = useCallback((filename) => {
    ocrFilenameRef.current = filename;
    setOcrPolling(true);
    setOcrPageInfo(null);

    ocrPollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/ocr-status`);
        const data = await res.json();

        const inProgress = data.in_progress || {};
        const completed  = data.completed  || {};

        if (inProgress[filename]) {
          // Still processing — update page counters
          const { page, total_pages } = inProgress[filename];
          setOcrPageInfo({ page, total_pages });
          setUploadProgress(total_pages ? Math.round((page / total_pages) * 90) : 50);
          setUploadStatus(`⏳ OCR processing… page ${page} / ${total_pages}`);
        } else if (completed[filename]) {
          // Done (success or error)
          const info = completed[filename];
          stopOcrPoll();
          setUploadProgress(100);
          setUploadStatus("");
          if (info.error) {
            setUploadBanner({ severity: "error", message: `OCR finished with error: ${info.error}` });
          } else {
            setUploadBanner({ severity: "success", message: "OCR complete — document is ready to query" });
          }
          fetchFiles(); // refresh dropdown after OCR finishes
          setTimeout(() => setUploadProgress(0), 1200);
        }
        // If neither key exists yet, the task just hasn't started — keep waiting
      } catch (_) {
        // Network hiccup — keep polling
      }
    }, 3000); // poll every 3 s
  }, [stopOcrPoll, fetchFiles]);

  // Clean up interval on unmount
  useEffect(() => () => stopOcrPoll(), [stopOcrPoll]);

  // Simplified Excel download using pre-formatted excel_data from backend
  function downloadAsExcel(data) {
    if (!data || !Array.isArray(data) || data.length === 0) {
      alert("No data available for Excel export");
      return;
    }

    console.log("Excel data received:", data);

    // Create BOM Data worksheet from excel_data
    const worksheet = XLSX.utils.json_to_sheet(data);

    // Set column widths for readability
    worksheet["!cols"] = [
      { wch: 8 },   // BOM No
      { wch: 22 },  // Parent Part Number
      { wch: 18 },  // LibRef
      { wch: 20 },  // Requested Part
      { wch: 15 },  // ComID
      { wch: 30 },  // Manufacturer Part Number
      { wch: 30 },  // Manufacturer Name
      { wch: 30 },  // PlName
      { wch: 50 },  // Description
      { wch: 40 },  // Datasheet
      { wch: 10 },  // EOL
      { wch: 10 },  // RoHS
      { wch: 20 },  // RoHS Version
      { wch: 50 },  // TaxonomyPath
      { wch: 20 },  // TaxonomyPathID
      { wch: 10 },  // YEOL
      { wch: 10 }   // Preference
    ];

    // Apply conditional formatting: red background for YEOL < 10
    // Skip for large datasets (>500 rows) — cell-by-cell mutation freezes the browser
    if (data.length <= 500) {
      data.forEach((row, idx) => {
        const rowIndex = idx + 2; // +2 because row 1 is header, data starts at row 2
        const yeolValue = parseFloat(row.YEOL);
        
        if (!isNaN(yeolValue) && yeolValue < 10) {
          // Apply red fill to all cells in this row
          const columns = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q'];
          columns.forEach(col => {
            const cellAddress = `${col}${rowIndex}`;
            if (!worksheet[cellAddress]) return;
            
            worksheet[cellAddress].s = {
              fill: {
                fgColor: { rgb: "FF0000" } // Red background
              },
              font: {
                color: { rgb: "FFFFFF" } // White text for contrast
              }
            };
          });
        }
      });
    }

    const workbook = XLSX.utils.book_new();

    // Add BOM Data sheet FIRST
    XLSX.utils.book_append_sheet(workbook, worksheet, "BOM Data");

    // Add Summary sheet
    const summaryRows = [
      { Field: "Report Generated", Value: new Date().toLocaleString() },
      { Field: "Total Parts", Value: data.length },
      { Field: "Primary Manufacturers", Value: data.filter(r => r.Preference === 1).length },
      { Field: "Alternative Manufacturers", Value: data.filter(r => r.Preference > 1).length },
      { Field: "EOL Risk (YEOL < 5 or unknown)", Value: data.filter(r => {
        const raw = r.YEOL; const yeol = parseFloat(raw);
        const missing = raw === null || raw === undefined || String(raw).trim() === "" || String(raw).trim().toLowerCase() === "no" || String(raw).trim().toLowerCase() === "undefined" || isNaN(yeol);
        return missing || yeol < 5;
      }).length }
    ];
    const summarySheet = XLSX.utils.json_to_sheet(summaryRows);
    summarySheet["!cols"] = [{ wch: 25 }, { wch: 40 }];
    XLSX.utils.book_append_sheet(workbook, summarySheet, "Summary");

    // Add Report sheet — parts with YEOL < 5 OR missing/non-numeric YEOL
    const eolRiskRows = data
      .filter(r => {
        const raw = r.YEOL;
        const yeol = parseFloat(raw);
        const isMissing = raw === null || raw === undefined || String(raw).trim() === ""
          || String(raw).trim().toLowerCase() === "no"
          || String(raw).trim().toLowerCase() === "undefined"
          || isNaN(yeol);
        return isMissing || yeol < 5;
      })
      .map(r => ({
        "BOM Part Number":        r["Requested Part"] || r["BOM No"] || "",
        "Manufacturer Part No":   r["Manufacturer Part Number"] || "",
        "Manufacturer":           r["Manufacturer Name"] || "",
        "Years to EOL":           r.YEOL,
        "Lifecycle":              r.EOL || "",
        "RoHS":                   r.RoHS || "",
        "Preference":             r.Preference || "",
      }));

    if (eolRiskRows.length > 0) {
      const reportSheet = XLSX.utils.json_to_sheet(eolRiskRows);
      reportSheet["!cols"] = [
        { wch: 22 }, // BOM Part Number
        { wch: 30 }, // Manufacturer Part No
        { wch: 30 }, // Manufacturer
        { wch: 14 }, // Years to EOL
        { wch: 18 }, // Lifecycle
        { wch: 10 }, // RoHS
        { wch: 12 }, // Preference
      ];
      // Red for YEOL < 5, orange for missing/unknown
      eolRiskRows.forEach((row, idx) => {
        const rowIndex = idx + 2;
        const yeol = parseFloat(row["Years to EOL"]);
        const isMissing = isNaN(yeol);
        const bgColor = isMissing ? "FFE0B2" : "FFCCCC"; // orange vs light-red
        ['A','B','C','D','E','F','G'].forEach(col => {
          const cell = `${col}${rowIndex}`;
          if (!reportSheet[cell]) return;
          reportSheet[cell].s = {
            fill: { fgColor: { rgb: bgColor } },
            font: { bold: col === 'D' }
          };
        });
      });
      XLSX.utils.book_append_sheet(workbook, reportSheet, "Report");
    }

    // Download the file with timestamp
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
    XLSX.writeFile(workbook, `BOM_SiliconExpert_Report_${timestamp}.xlsx`);
  }

  // Upload handler
  const handleUpload = async () => {
    if (!file) return;

    // Stop any previous poll
    stopOcrPoll();
    setUploadBanner(null);
    setUploadStatus("Uploading…");
    setUploadProgress(20);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("ocr_dpi", ocrDpi);

    try {
      const response = await fetch(`${API_BASE}/upload`, {
        method: "POST",
        body: formData,
      });
      const result = await response.json();

      if (!response.ok && response.status !== 202) {
        throw new Error(result.message || result.detail || `Upload failed with status ${response.status}`);
      }

      if (response.status === 202) {
        // Image-based PDF — OCR running in background
        const filename = file.name;
        setUploadStatus(`⏳ OCR processing started for "${filename}"…`);
        setUploadProgress(10);
        startOcrPoll(filename);
      } else {
        // Text-based PDF or BOM .txt — indexed immediately
        setUploadProgress(100);
        setTimeout(() => setUploadProgress(0), 600);
        setUploadBanner({ severity: "success", message: result.message || "Upload successful" });
        setUploadStatus("");
        fetchFiles(); // refresh dropdown
      }
    } catch (err) {
      setUploadBanner({ severity: "error", message: err.message || "Upload failed. Please try again." });
      setUploadStatus("");
      setUploadProgress(0);
    }
  };

  // // Query handler (no chat history)
  // const handleQuery = async (e) => {
  //   e.preventDefault();
  //   if (!query.trim()) return;
  //   setLoading(true);
  //   setAnswer(""); // Clear previous answer
  //   try {
  //     const response = await fetch("http://localhost:8000/query", {
  //       method: "POST",
  //       headers: { "Content-Type": "application/json" },
  //       body: JSON.stringify({ query }), // Only send the query
  //     });
  //     const result = await response.json();
  //     setAnswer(result.answer || "No answer found.");
  //     setTimeout(() => {
  //       if (answerRef.current) {
  //         answerRef.current.scrollIntoView({ behavior: "smooth" });
  //       }
  //     }, 100);
  //   } catch (err) {
  //     setAnswer("Error fetching answer.");
  //   }
  //   setLoading(false);
  //   setQuery("");
  // };


// const handleQuery = async (e) => {
//   e.preventDefault();
//   if (!query.trim()) return;
//   setLoading(true);
//   setAnswer(""); // Clear previous answer
//   try {
//     const response = await fetch("http://localhost:8000/query", {
//       method: "POST",
//       headers: { "Content-Type": "application/json" },
//       body: JSON.stringify({ query }),
//     });
//     const reader = response.body.getReader();
//     const decoder = new TextDecoder();
//     let answerText = "";
//     while (true) {
//       const { value, done } = await reader.read();
//       if (done) break;
//       const chunk = decoder.decode(value);
//       // Typing effect: add each character with a small delay
//       for (let char of chunk) {
//         answerText += char;
//         setAnswer(answerText);
//         await new Promise(res => setTimeout(res, 10)); // 10ms delay per character
//       }
//     }

//     const extractedJson = extractJsonFromMarkdown(answerText);
//     if (extractedJson) {
//       console.log("Extracted SiliconExpert data:", extractedJson);
//       setSiliconExpertData(extractedJson);
//     } else {
//       console.log("No JSON found in answer");
//     }

//     setTimeout(() => {
//       if (answerRef.current) {
//         answerRef.current.scrollIntoView({ behavior: "smooth" });
//       }
//     }, 100);
//   } catch (err) {
//     setAnswer("Error fetching answer.");
//   }
//   setLoading(false);
//   setQuery("");
// };


const handleQuery = async (e) => {
  if (e && e.preventDefault) e.preventDefault();
  if (!query.trim()) return;
  setLoading(true);
  setAnswer(""); // Clear previous answer
  setExcelData(null); // Clear previous Excel data
  
  try {
    const response = await fetch(`${API_BASE}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, filename: selectedFile || undefined }),
    });
    
    const result = await response.json();
    
    // Extract formatted_response for display
    const formattedResponse = result.formatted_response || result.message || "No answer found.";
    setAnswer(formattedResponse);
    
    // Extract excel_data for download
    if (result.excel_data && Array.isArray(result.excel_data) && result.excel_data.length > 0) {
      console.log("Excel data extracted:", result.excel_data);
      setExcelData(result.excel_data);
      setDashboardRows(result.excel_data);
      localStorage.setItem("latestDashboardRows", JSON.stringify(result.excel_data));
    } else {
      console.log("No excel_data in response");
      setExcelData(null);
      setDashboardRows([]);
      localStorage.removeItem("latestDashboardRows");
    }

    setTimeout(() => {
      if (answerRef.current) {
        answerRef.current.scrollIntoView({ behavior: "smooth" });
      }
    }, 100);
  } catch (err) {
    setAnswer("Error fetching answer.");
    console.error("Query error:", err);
  }
  setLoading(false);
  setQuery("");
};

  // Re-index handler
  const handleReindex = async () => {
    setReindexing(true);
    setReindexStatus("Re-indexing...");
    setTimeout(() => setReindexStatus("Almost done..."), 500);
    try {
      const response = await fetch(`${API_BASE}/reindex`, { method: "POST" });
      const result = await response.json();
      setReindexStatus(result.status || "Re-indexed.");
    } catch (err) {
      setReindexStatus("Re-index failed.");
    }
    setTimeout(() => setReindexing(false), 1000);
  };

  const dashboardMetrics = getDashboardMetrics(dashboardRows);
  const maxLifecycleCount = Math.max(...Object.values(dashboardMetrics.lifecycleCounts), 1);
  const dashboardTableRows = dashboardRows.slice(0, 8);

  if (activeView === "dashboard") {
    return (
      <Box sx={{ minHeight: "100vh", bgcolor: "#1e1f21", p: { xs: 1.5, md: 3 } }}>
        <Box sx={{ maxWidth: 1420, mx: "auto", bgcolor: "#edf1f6", minHeight: "calc(100vh - 48px)", boxShadow: "0 22px 60px rgba(0,0,0,0.35)" }}>
          <Box sx={{
            height: 64, px: { xs: 2, md: 4 }, display: "flex", alignItems: "center", justifyContent: "space-between",
            background: "linear-gradient(90deg, #07386f 0%, #0b66a5 100%)", color: "#fff",
            boxShadow: "0 2px 8px rgba(0,0,0,0.22)",
          }}>
            <Box sx={{ width: 120, display: { xs: "none", sm: "block" } }} />
            <Typography variant="h5" sx={{ fontWeight: 900, letterSpacing: 0.2, textAlign: "center", fontFamily: "Georgia, 'Times New Roman', serif" }}>
              Obsolescence Management Dashboard
            </Typography>
            <Stack direction="row" spacing={1} alignItems="center">
              <Tooltip title="Upload and query">
                <IconButton onClick={() => setActiveView("workspace")} sx={{ color: "#fff" }} aria-label="open upload and query workspace">
                  <Dashboard />
                </IconButton>
              </Tooltip>
              <ShieldOutlined />
              <Help />
            </Stack>
          </Box>

          <Box sx={{ p: { xs: 2, md: 3 }, display: "grid", gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" }, gap: 2 }}>
            <Paper elevation={2} sx={{ borderRadius: 1, p: 2.3, border: "1px solid #c7d2e2", bgcolor: "#f8fafc" }}>
              <Typography variant="h6" sx={{ fontWeight: 900, color: "#18345a", pb: 1, borderBottom: "2px solid #c8d2df", fontFamily: "Georgia, 'Times New Roman', serif" }}>
                Obsolescence Risk Overview
              </Typography>

              <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" }, mt: 2, mb: 2, borderBottom: "2px solid #c8d2df", pb: 2 }}>
                {[
                  { icon: <Warning sx={{ fontSize: 46, color: "#e1261c" }} />, label: "High Risk Parts:", value: dashboardMetrics.highRisk, color: "#e1261c" },
                  { icon: <ReceiptLong sx={{ fontSize: 44, color: "#f28b00" }} />, label: "EOL / NRND Components:", value: dashboardMetrics.eolNrnd, color: "#18345a" },
                  { icon: <Assignment sx={{ fontSize: 44, color: "#ffc400" }} />, label: "Pending PCNs:", value: dashboardMetrics.pendingPcn, color: "#18345a" },
                ].map((item, index) => (
                  <Box key={item.label} sx={{
                    display: "flex", alignItems: "center", gap: 1.2, px: { xs: 0, sm: 1.5 }, py: { xs: 1, sm: 0 },
                    borderLeft: index === 0 ? "none" : { xs: "none", sm: "2px solid #d2dbe7" },
                  }}>
                    {item.icon}
                    <Box>
                      <Typography sx={{ fontWeight: 900, color: "#18345a", lineHeight: 1.05 }}>{item.label}</Typography>
                      <Typography component="span" sx={{ fontSize: 28, fontWeight: 900, color: item.color, lineHeight: 1 }}>{item.value}</Typography>
                    </Box>
                  </Box>
                ))}
              </Box>

              <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1.25fr" }, gap: 3, alignItems: "end" }}>
                <Box>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
                    <Typography variant="h6" sx={{ fontWeight: 900, color: "#17345b", fontFamily: "Georgia, 'Times New Roman', serif" }}>
                      Overall Risk Score
                    </Typography>
                    <Help sx={{ color: "#17345b" }} />
                  </Stack>
                  <Box sx={{ width: 250, maxWidth: "100%", mx: "auto", position: "relative", aspectRatio: "2 / 1", overflow: "hidden" }}>
                    <Box sx={{
                      position: "absolute", inset: 0, borderRadius: "250px 250px 0 0",
                      background: "conic-gradient(from 270deg, #23a329 0deg 52deg, #b7d818 52deg 75deg, #f4dc18 75deg 112deg, #ff8b00 112deg 145deg, #d7191c 145deg 180deg, transparent 180deg 360deg)",
                    }} />
                    <Box sx={{ position: "absolute", left: "23%", right: "23%", bottom: 0, height: "54%", bgcolor: "#f8fafc", borderRadius: "130px 130px 0 0" }} />
                    <Typography sx={{ position: "absolute", left: 26, bottom: 28, color: "#fff", fontWeight: 900 }}>Low</Typography>
                    <Typography sx={{ position: "absolute", left: "39%", top: 18, color: "#fff", fontWeight: 900, fontSize: 22 }}>Moderate</Typography>
                    <Typography sx={{ position: "absolute", right: 20, bottom: 28, color: "#fff", fontWeight: 900 }}>High</Typography>
                    <Box sx={{ position: "absolute", left: "50%", bottom: 11, width: 116, height: 7, bgcolor: "#18345a", transformOrigin: "0 50%", transform: `rotate(${Math.min(178, Math.max(2, dashboardMetrics.score * 1.8)) - 180}deg)`, borderRadius: 2 }} />
                    <Box sx={{ position: "absolute", left: "50%", bottom: 0, transform: "translateX(-50%)", width: 72, height: 56, borderRadius: "50%", bgcolor: "#18345a", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 900, fontSize: 32, boxShadow: "0 3px 8px rgba(0,0,0,0.25)" }}>
                      {dashboardMetrics.score}
                    </Box>
                  </Box>
                </Box>

                <Box>
                  <Typography variant="h6" sx={{ fontWeight: 900, color: "#17345b", pb: 1, mb: 2, borderBottom: "2px solid #c8d2df", fontFamily: "Georgia, 'Times New Roman', serif" }}>
                    Lifecycle Status Distribution
                  </Typography>
                  <Box sx={{ height: 160, display: "grid", gridTemplateColumns: "repeat(4, 1fr)", alignItems: "end", gap: 2, borderBottom: "2px solid #7d92b0", background: "repeating-linear-gradient(to top, transparent 0, transparent 22px, #c6d0dc 23px)" }}>
                    {[
                      ["Active", "#3d66a0"],
                      ["NRND", "#78b813"],
                      ["EOL", "#ef3124"],
                      ["PCN", "#ffb000"],
                    ].map(([status, color]) => (
                      <Box key={status} sx={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "flex-end", height: "100%" }}>
                        <Box sx={{ width: "58%", minHeight: 8, height: `${Math.max(8, (dashboardMetrics.lifecycleCounts[status] / maxLifecycleCount) * 130)}px`, bgcolor: color, boxShadow: "inset 0 1px 0 rgba(255,255,255,0.3)" }} />
                        <Typography sx={{ fontSize: 13, fontWeight: 900, color: "#35445a", mt: 0.8 }}>{status}</Typography>
                      </Box>
                    ))}
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
                      <Select value={dashboardFile} onChange={e => setDashboardFile(e.target.value)} displayEmpty>
                        {files.length === 0 && <MenuItem value="">No indexed BOMs</MenuItem>}
                        {files.map(item => <MenuItem key={item.filename} value={item.filename}>{item.filename}</MenuItem>)}
                      </Select>
                    </FormControl>
                    <Tooltip title="Refresh dashboard">
                      <IconButton onClick={() => fetchDashboardData(dashboardFile)} disabled={!dashboardFile || dashboardLoading} aria-label="refresh dashboard">
                        <Autorenew />
                      </IconButton>
                    </Tooltip>
                    <Settings sx={{ color: "#244d7d" }} />
                  </Stack>
                </Stack>

                <Stack direction={{ xs: "column", sm: "row" }} spacing={2.5} alignItems={{ xs: "flex-start", sm: "center" }} sx={{ py: 1.5 }}>
                  <Typography sx={{ fontWeight: 900, color: "#17345b" }}>Total Parts: {dashboardMetrics.totalParts}</Typography>
                  <Divider orientation="vertical" flexItem sx={{ display: { xs: "none", sm: "block" } }} />
                  <Typography sx={{ fontWeight: 900, color: "#17345b" }}>At Risk: {dashboardMetrics.atRisk}</Typography>
                  <CheckCircle sx={{ color: dashboardMetrics.atRisk ? "#bd2217" : "#238636" }} />
                  <Divider orientation="vertical" flexItem sx={{ display: { xs: "none", sm: "block" } }} />
                  <Typography sx={{ fontWeight: 900, color: "#17345b" }}>Last Updated: {formatDate()}</Typography>
                </Stack>
              </Box>

              {dashboardLoading && <LinearProgress />}
              {dashboardError && <Alert severity="warning" sx={{ mx: 2.3, mb: 1.5 }}>{dashboardError}</Alert>}

              <TableContainer sx={{ px: 0 }}>
                <Table size="small" sx={{ tableLayout: "fixed" }}>
                  <TableHead>
                    <TableRow sx={{ bgcolor: "#526786" }}>
                      {["Part Number", "Description", "Status", "YEOL"].map(header => (
                        <TableCell key={header} sx={{ color: "#fff", fontWeight: 900, fontSize: 16, borderColor: "#8fa0b8" }}>{header}</TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {dashboardTableRows.map((row, index) => {
                      const status = normalizeLifecycleStatus(row.EOL, row.YEOL);
                      const chip = statusColor(status);
                      return (
                        <TableRow key={`${row["Requested Part"]}-${index}`} sx={{ bgcolor: index % 2 ? "#f4f6f9" : "#fff" }}>
                          <TableCell sx={{ fontWeight: 900 }}>{row["Parent Part Number"] || row["Requested Part"] || row["Manufacturer Part Number"] || "-"}</TableCell>
                          <TableCell sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.Description || row.PlName || "-"}</TableCell>
                          <TableCell>
                            <Chip label={status} size="small" sx={{ bgcolor: chip.bg, color: chip.color, fontWeight: 900, minWidth: 68 }} />
                          </TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>{row.YEOL || "-"}</TableCell>
                        </TableRow>
                      );
                    })}
                    {!dashboardLoading && dashboardTableRows.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={4} sx={{ py: 4, textAlign: "center", color: "text.secondary" }}>
                          No dashboard rows available. Upload/index a BOM or refresh after querying lifecycle data.
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

  const queryPage = (
    <Box sx={{ minHeight: "100vh", background: "linear-gradient(160deg, #e8edf5 0%, #f2f4f8 45%, #eceef2 100%)" }}>
    <Container maxWidth={false} sx={{ py: 5, px: { xs: 2, sm: 4, md: 6 }, display: "flex", justifyContent: "center" }}>
      <Box sx={{ width: "100%", maxWidth: "60%" }}>
      <Paper
        elevation={0}
        sx={{
          borderRadius: 4, overflow: "hidden",
          border: "1px solid rgba(25,118,210,0.1)",
          boxShadow: "0 8px 40px rgba(25,118,210,0.13), 0 2px 10px rgba(0,0,0,0.06)",
          transition: "box-shadow 0.3s ease",
          "&:hover": { boxShadow: "0 12px 50px rgba(25,118,210,0.18), 0 4px 16px rgba(0,0,0,0.08)" },
        }}
      >
        {/* ── Gradient header bar ─────────────────────────────── */}
        <Box sx={{ background: "linear-gradient(135deg, #002868 0%, #0063BE 100%)", px: 4, py: 3, borderBottom: "3px solid #00509E" }}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ xs: "flex-start", sm: "center" }} justifyContent="space-between">
            <Box>
              <Typography variant="h5" fontWeight={800} sx={{ color: "#fff", letterSpacing: 0.3 }}>
                BOM Obsolescence Analyzer
              </Typography>
              <Typography variant="body2" sx={{ color: "rgba(255,255,255,0.78)", mt: 0.5 }}>
                Upload, query, and export component lifecycle data
              </Typography>
            </Box>
            <Button
              variant="outlined"
              startIcon={<SmartToyOutlined />}
              component={RouterLink}
              to="/dashboard"
              state={{ rows: dashboardRows, filename: selectedFile }}
              sx={{ color: "#fff", borderColor: "rgba(255,255,255,0.55)", textTransform: "none", fontWeight: 700, "&:hover": { borderColor: "#fff", bgcolor: "rgba(255,255,255,0.08)" } }}
            >
              Dashboard
            </Button>
          </Stack>
        </Box>
        <Box sx={{ p: 4 }}>

        {/* ── Upload Section ─────────────────────────────────────── */}
        <Typography variant="h6" fontWeight={700} gutterBottom sx={{
          display: "flex", alignItems: "center", gap: 1.5,
          "&::before": { content: '""', display: "block", width: 4, height: 20, borderRadius: 2, bgcolor: "primary.main", flexShrink: 0 },
        }}>
          Upload BOM Document
        </Typography>

        {/* ── Step 1: Choose file ─────────────────────────────────── */}
        <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
          <Button
            variant="outlined"
            component="label"
            startIcon={<CloudUpload />}
            sx={{
              textTransform: "none", minWidth: 160, fontWeight: 600,
              borderWidth: 1.5,
              transition: "all 0.25s ease",
              "&:hover": { borderWidth: 1.5, transform: "translateY(-1px)", boxShadow: "0 4px 12px rgba(25,118,210,0.15)" },
              "&:active": { transform: "translateY(0)" },
            }}
          >
            {file ? file.name : "Choose file…"}
            <input
              type="file"
              hidden
              onChange={e => { setFile(e.target.files[0]); setUploadBanner(null); }}
            />
          </Button>

          <Tooltip
            title={
              <Box sx={{ p: 0.5, minWidth: 220 }}>
                <Typography
                  variant="caption"
                  sx={{ fontWeight: 700, fontSize: "0.75rem", display: "block",
                    borderBottom: "1px solid rgba(255,255,255,0.25)", pb: 0.75, mb: 1 }}
                >
                  Supported File Types
                </Typography>
                {[
                  { icon: "📄", label: "PDF",        exts: ".pdf" },
                  { icon: "📝", label: "Text",       exts: ".txt, .text" },
                  { icon: "📊", label: "Excel",      exts: ".xlsx, .xls" },
                  { icon: "🗂️", label: "CSV",        exts: ".csv" },
                  { icon: "📘", label: "Word",       exts: ".docx, .doc" },
                  { icon: "📑", label: "PowerPoint", exts: ".pptx, .ppt" },
                  { icon: "🖼️", label: "Images",     exts: ".png .jpg .jpeg .bmp .tiff .gif .webp" },
                ].map(({ icon, label, exts }) => (
                  <Box key={label} sx={{ display: "flex", alignItems: "baseline", gap: 0.75, mb: 0.5 }}>
                    <span style={{ fontSize: "0.8rem" }}>{icon}</span>
                    <Typography variant="caption" sx={{ fontWeight: 600, minWidth: 82, fontSize: "0.72rem" }}>
                      {label}
                    </Typography>
                    <Typography variant="caption" sx={{ opacity: 0.75, fontSize: "0.68rem", fontFamily: "monospace" }}>
                      {exts}
                    </Typography>
                  </Box>
                ))}
              </Box>
            }
            placement="right"
            arrow
            componentsProps={{
              tooltip: { sx: { maxWidth: 280, bgcolor: "grey.900", fontSize: "0.75rem" } },
              arrow:   { sx: { color: "grey.900" } }
            }}
          >
            <IconButton size="small" color="info" aria-label="supported file types">
              <InfoOutlined fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>

        {/* ── Step 2: OCR Quality selector + Upload button (visible once a file is chosen) ── */}
        {file && (
          <Stack direction="column" alignItems="flex-start" spacing={2} sx={{ mb: 1 }}>
            <Tooltip
              title="Higher DPI = better accuracy for dense tables but slower processing. 300 DPI is the effective maximum supported by the OCR engine."
              placement="right"
            >
              <FormControl size="small" sx={{ minWidth: 260, maxWidth: 400 }}>
                <InputLabel id="dpi-label">OCR Quality (DPI)</InputLabel>
                <Select
                  labelId="dpi-label"
                  value={ocrDpi}
                  label="OCR Quality (DPI)"
                  onChange={e => setOcrDpi(e.target.value)}
                  disabled={ocrPolling}
                >
                  <MenuItem value={96}>96 DPI — Fast, lower accuracy</MenuItem>
                  <MenuItem value={150}>150 DPI — Balanced</MenuItem>
                  <MenuItem value={200}>200 DPI — Recommended (default)</MenuItem>
                  <MenuItem value={300}>300 DPI — Maximum accuracy, slower</MenuItem>
                </Select>
              </FormControl>
            </Tooltip>
            {/* ── Step 3: Upload button ──────────────────────────── */}
            <Button
              variant="contained"
              onClick={handleUpload}
              disabled={!file || ocrPolling}
              startIcon={ocrPolling ? <CircularProgress size={16} sx={{ color: "#fff" }} /> : <CloudUpload />}
              sx={{
                textTransform: "none", fontWeight: 600,
                color: "#fff", width: "fit-content", minWidth: 130,
                background: "linear-gradient(135deg, #002868 0%, #0063BE 100%)",
                boxShadow: "0 4px 15px rgba(0,40,104,0.35)",
                transition: "all 0.25s ease",
                "&:hover": { boxShadow: "0 6px 20px rgba(0,40,104,0.5)", transform: "translateY(-1px)" },
                "&:active": { transform: "translateY(0)" },
                "&.Mui-disabled": { background: "rgba(0,0,0,0.12)", color: "rgba(0,0,0,0.26)" },
              }}
            >
              {ocrPolling ? "Processing…" : "Upload"}
            </Button>
          </Stack>
        )}

        {/* Progress bar — orange during OCR, blue otherwise */}
        {uploadProgress > 0 && (
          <LinearProgress
            variant="determinate"
            value={uploadProgress}
            sx={{
              mt: 2, height: 8, borderRadius: 4,
              "& .MuiLinearProgress-bar": {
                backgroundColor: ocrPolling ? "#f57c00" : "#1976d2"
              }
            }}
          />
        )}

        {/* In-progress status text (upload / OCR) */}
        {uploadStatus && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {uploadStatus}
          </Typography>
        )}

        {/* Page-by-page OCR badge */}
        {ocrPolling && ocrPageInfo && (
          <Alert
            icon={false}
            severity="warning"
            sx={{ mt: 1.5, py: 0.5, alignItems: "center" }}
          >
            📄 Page {ocrPageInfo.page} of {ocrPageInfo.total_pages} — OCR in progress,
            document will be queryable when complete
          </Alert>
        )}

        <Divider sx={{ my: 3, borderColor: "rgba(25,118,210,0.12)" }} />

        {/* ── Query Section ──────────────────────────────────────── */}
        <Typography variant="h6" fontWeight={700} gutterBottom sx={{
          display: "flex", alignItems: "center", gap: 1.5,
          "&::before": { content: '""', display: "block", width: 4, height: 20, borderRadius: 2, bgcolor: "secondary.main", flexShrink: 0 },
        }}>
          Ask a Query
        </Typography>

        {/* ── BOM File Filter ─────────────────────────────────────── */}
        {files.length > 0 && (
          <FormControl size="small" sx={{ mb: 1.5, minWidth: 320, maxWidth: 480 }}>
            <InputLabel id="file-select-label">Filter by BOM file (optional)</InputLabel>
            <Select
              labelId="file-select-label"
              value={selectedFile}
              label="Filter by BOM file (optional)"
              onChange={e => setSelectedFile(e.target.value)}
              disabled={loading}
            >
              <MenuItem value="">None (search all files)</MenuItem>
              {files.map(f => (
                <MenuItem key={f.filename} value={f.filename}>
                  {f.filename}
                  {f.status === "ocr_processing" && " ⏳"}
                  {f.status === "ocr_failed" && " ⚠️"}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}

        <Stack direction="row" spacing={1.5} alignItems="flex-start">
          <TextField
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === "Enter" && !loading && query.trim() && handleQuery(e)}
            placeholder="Enter your question…"
            size="small"
            fullWidth
            disabled={loading}
          />
          <Button
            variant="contained"
            onClick={handleQuery}
            disabled={loading || !query.trim()}
            startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <Search />}
            sx={{
              textTransform: "none", whiteSpace: "nowrap", fontWeight: 600,
              background: "linear-gradient(135deg, #1976d2 0%, #1565c0 100%)",
              boxShadow: "0 4px 15px rgba(25,118,210,0.35)",
              transition: "all 0.25s ease",
              "&:hover": { boxShadow: "0 6px 20px rgba(25,118,210,0.5)", transform: "translateY(-1px)" },
              "&:active": { transform: "translateY(0)" },
              "&.Mui-disabled": { background: "rgba(0,0,0,0.12)", boxShadow: "none" },
            }}
          >
            {loading ? "Searching…" : "Ask"}
          </Button>
        </Stack>

        {/* Answer box */}
<Paper
  variant="outlined"
  sx={{
    mt: 2,
    p: 2,
    height: "100vh",
    minHeight: 280,
    flexShrink: 0, // fixed height
    borderRadius: 2,
    background: "linear-gradient(135deg, #f8faff 0%, #faf8ff 100%)",
    border: "1px solid rgba(25,118,210,0.15)",
    fontFamily: "monospace",
    fontSize: "0.88rem",
    whiteSpace: "pre-wrap",
    overflowY: "auto",
    overflowX: "auto",
    color: "#333",
    scrollbarWidth: "thin",
    "&::-webkit-scrollbar": {
      width: "8px",
      height: "8px",
    },
    "&::-webkit-scrollbar-thumb": {
      backgroundColor: "#b0bec5",
      borderRadius: "4px",
    },
    "&::-webkit-scrollbar-thumb:hover": {
      backgroundColor: "#90a4ae",
    },
  }}
  ref={answerRef}
>
          {answer || <Typography variant="body2" color="text.disabled">Answer will appear here…</Typography>}
        </Paper>

      
        <Divider sx={{ my: 3, borderColor: "rgba(25,118,210,0.12)" }} />

        {/* ── Re-index Section ───────────────────────────────────── */}
        <Stack direction="row" spacing={2} alignItems="center">
          <Button
            variant="outlined"
            color="secondary"
            onClick={handleReindex}
            disabled={reindexing}
            startIcon={reindexing ? <CircularProgress size={16} color="inherit" /> : <Autorenew />}
            sx={{
              textTransform: "none", fontWeight: 600,
              borderWidth: 1.5,
              transition: "all 0.25s ease",
              "&:hover": { borderWidth: 1.5, transform: "translateY(-1px)", boxShadow: "0 4px 12px rgba(156,39,176,0.2)" },
              "&:active": { transform: "translateY(0)" },
            }}
          >
            {reindexing ? "Re-indexing…" : "Re-index Documents"}
          </Button>

          {reindexStatus && (
            <Typography variant="body2" color="text.secondary">
              {reindexStatus}
            </Typography>
          )}
        </Stack>

        </Box>{/* close inner content Box */}
      </Paper>
      {excelData && excelData.length > 0 && (
  <Paper
    elevation={6}
    sx={{
      position: "sticky", bottom: 0, zIndex: 10, mt: 2, px: 3, py: 1.5,
      borderRadius: "0 0 0 0", border: "1px solid rgba(25,118,210,0.2)",
      display: "flex", flexWrap: "wrap", alignItems: "center",
      justifyContent: "space-between", gap: 1.5, bgcolor: "#fff",
    }}
  >
    <Typography variant="body2" color="text.secondary">
      {excelData.length} result row(s) ready
    </Typography>
    <Stack direction="row" spacing={1.5}>
      <Button
        variant="outlined"
        component={RouterLink}
        to="/dashboard"
        state={{ rows: excelData, filename: selectedFile }}
        startIcon={<SmartToyOutlined />}
        sx={{ textTransform: "none", fontWeight: 700 }}
      >
        View Dashboard
      </Button>
      <Button
        variant="contained"
        color="success"
        startIcon={<Download />}
        onClick={() => downloadAsExcel(excelData)}
        sx={{ textTransform: "none", fontWeight: 600 }}
      >
        Download Excel Report&nbsp;
        <Chip
          label={excelData.length}
          size="small"
          sx={{ ml: 0.5, background: "rgba(255,255,255,0.3)", color: "#fff", fontWeight: 700 }}
        />
      </Button>
    </Stack>
  </Paper>
)}
      {/* ── Upload toast notification ─────────────────────────── */}
      <Snackbar
        open={!!uploadBanner}
        autoHideDuration={5000}
        onClose={(_, reason) => { if (reason !== "clickaway") setUploadBanner(null); }}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      >
        <Alert
          severity={uploadBanner?.severity || "info"}
          onClose={() => setUploadBanner(null)}
          variant="filled"
          sx={{ width: "100%" }}
        >
          {uploadBanner?.message}
        </Alert>
      </Snackbar>
      </Box>{/* close 60% width Box */}
    </Container>
    </Box>
  );

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={queryPage} />
        <Route
          path="/dashboard"
          element={
            <DashboardPage
              apiBase={API_BASE}
              files={files}
              initialRows={dashboardRows}
              selectedFilename={selectedFile}
              onRowsChange={setDashboardRows}
            />
          }
        />
      </Routes>
    </BrowserRouter>
  );
}

export default App;