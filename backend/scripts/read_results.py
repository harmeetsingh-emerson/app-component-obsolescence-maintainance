"""Read and display key lines from test_results.txt."""
import sys

with open("test_results.txt", encoding="utf-16-le") as f:
    content = f.read()

KEY_WORDS = ["TEST:", "Message   :", "Excel rows :", "Parts found:", "EXCEPTION",
             "BOM#", "more rows", "Formatted response", "response (first"]

for line in content.splitlines():
    s = line.strip()
    if any(kw in s for kw in KEY_WORDS):
        print(s)
