import React, { useState, useRef, useEffect, useCallback } from "react";
import * as XLSX from 'xlsx';
import {
  Button, Typography, LinearProgress, Paper, TextField,
  Alert, Chip, CircularProgress, Divider, Container, Stack,
  Select, MenuItem, FormControl, InputLabel, Tooltip
} from "@mui/material";
import {
  CloudUpload, Search, Download, Autorenew
} from "@mui/icons-material";


function App() {
  // API base URL — set via REACT_APP_API_URL at build time.
  // Production (single container): empty string → relative paths served by FastAPI.
  // Development (separate containers): http://localhost:8000
  const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:8000";

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
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState("");
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
          if (info.error) {
            setUploadStatus(`⚠️ OCR finished with error: ${info.error}`);
          } else {
            setUploadStatus(`✅ OCR complete — document is ready to query`);
          }          fetchFiles(); // refresh dropdown after OCR finishes          setTimeout(() => setUploadProgress(0), 1200);
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
    data.forEach((row, idx) => {
      const rowIndex = idx + 2; // +2 because row 1 is header, data starts at row 2
      const yeolValue = parseFloat(row.YEOL);
      
      if (!isNaN(yeolValue) && yeolValue < 10) {
        // Apply red fill to all cells in this row
        const columns = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O'];
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
    setUploadStatus("Uploading...");
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

      if (response.status === 202) {
        // Image-based PDF — OCR running in background
        const filename = file.name;
        setUploadStatus(`⏳ OCR processing started for "${filename}"…`);
        setUploadProgress(10);
        startOcrPoll(filename);
      } else {
        // Text-based PDF or BOM .txt — indexed immediately
        setAnswer(result.message || JSON.stringify(result));
        setUploadProgress(100);
        setTimeout(() => setUploadProgress(0), 600);
        setUploadStatus(`✅ ${result.message || 'Upload successful'}`);
        fetchFiles(); // refresh dropdown
      }
    } catch (err) {
      setUploadStatus("Upload failed.");
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
    } else {
      console.log("No excel_data in response");
      setExcelData(null);
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

  return (
    <Container maxWidth="sm" sx={{ py: 5 }}>
      <Paper elevation={3} sx={{ p: 4, borderRadius: 3 }}>

        {/* ── Upload Section ─────────────────────────────────────── */}
        <Typography variant="h5" fontWeight={700} gutterBottom>
          Upload Document
        </Typography>

        {/* ── OCR Quality (DPI) Selector ──────────────────────────── */}
        <Tooltip
          title="Higher DPI = better accuracy for dense tables but slower processing"
          placement="right"
        >
          <FormControl size="small" sx={{ mb: 2, minWidth: 260 }}>
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
              <MenuItem value={300}>300 DPI — High accuracy, slower</MenuItem>
              <MenuItem value={400}>400 DPI — Maximum accuracy, slowest</MenuItem>
            </Select>
          </FormControl>
        </Tooltip>

        <Stack direction="row" spacing={2} alignItems="center">
          <Button
            variant="outlined"
            component="label"
            startIcon={<CloudUpload />}
            sx={{ textTransform: "none", minWidth: 160 }}
          >
            {file ? file.name : "Choose file…"}
            <input type="file" hidden onChange={e => setFile(e.target.files[0])} />
          </Button>

          <Button
            variant="contained"
            onClick={handleUpload}
            disabled={!file || ocrPolling}
            startIcon={ocrPolling ? <CircularProgress size={16} color="inherit" /> : <CloudUpload />}
            sx={{ textTransform: "none" }}
          >
            {ocrPolling ? "Processing…" : "Upload"}
          </Button>
        </Stack>

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

        {/* Status text */}
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

        <Divider sx={{ my: 3 }} />

        {/* ── Query Section ──────────────────────────────────────── */}
        <Typography variant="h5" fontWeight={700} gutterBottom>
          Ask a Query
        </Typography>

        {/* ── BOM File Filter ─────────────────────────────────────── */}
        {files.length > 0 && (
          <FormControl size="small" sx={{ mb: 1.5, minWidth: 320 }}>
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
            sx={{ textTransform: "none", whiteSpace: "nowrap" }}
          >
            {loading ? "Searching…" : "Ask"}
          </Button>
        </Stack>

        {/* Answer box */}
        <Paper
          variant="outlined"
          sx={{
            mt: 2, p: 2, minHeight: 100, borderRadius: 2,
            background: "#f8f9fa", fontFamily: "monospace",
            fontSize: "0.88rem", whiteSpace: "pre-wrap",
            overflowX: "auto", color: "#333"
          }}
          ref={answerRef}
        >
          {answer || <Typography variant="body2" color="text.disabled">Answer will appear here…</Typography>}
        </Paper>

        {/* Download Excel */}
        {excelData && excelData.length > 0 && (
          <Button
            variant="contained"
            color="success"
            startIcon={<Download />}
            onClick={() => downloadAsExcel(excelData)}
            sx={{ mt: 2, textTransform: "none", fontWeight: 600 }}
            fullWidth
          >
            Download Excel Report&nbsp;
            <Chip
              label={excelData.length}
              size="small"
              sx={{ ml: 0.5, background: "rgba(255,255,255,0.3)", color: "#fff", fontWeight: 700 }}
            />
          </Button>
        )}

        <Divider sx={{ my: 3 }} />

        {/* ── Re-index Section ───────────────────────────────────── */}
        <Stack direction="row" spacing={2} alignItems="center">
          <Button
            variant="outlined"
            color="secondary"
            onClick={handleReindex}
            disabled={reindexing}
            startIcon={reindexing ? <CircularProgress size={16} color="inherit" /> : <Autorenew />}
            sx={{ textTransform: "none" }}
          >
            {reindexing ? "Re-indexing…" : "Re-index Documents"}
          </Button>

          {reindexStatus && (
            <Typography variant="body2" color="text.secondary">
              {reindexStatus}
            </Typography>
          )}
        </Stack>

      </Paper>
    </Container>
  );
}

export default App;