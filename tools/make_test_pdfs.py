"""Генерация синтетических тестовых статей (выдуманные данные) для проверки конвейера: python tools/make_test_pdfs.py"""
import pathlib
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

OUT = pathlib.Path("test_files"); OUT.mkdir(exist_ok=True)
ss = getSampleStyleSheet()
H = ParagraphStyle("H", parent=ss["Title"], fontSize=15, leading=19)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=8)
B = ParagraphStyle("B", parent=ss["BodyText"], fontSize=9.5, leading=13)
C = ParagraphStyle("C", parent=B, fontSize=8.5, textColor=colors.HexColor("#444444"))

def table(rows, widths):
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6eaf2")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t

def build(name, story):
    doc = SimpleDocTemplate(str(OUT / name), pagesize=A4, leftMargin=50, rightMargin=50, topMargin=50, bottomMargin=50,
                            title=name, author="Synthetic test document")
    doc.build(story)

# ---------- 1. Солнечные элементы ----------
solar = [
 Paragraph("Copper-Doped Sb<sub>2</sub>Se<sub>3</sub> Thin-Film Solar Cells Prepared by Close-Spaced Sublimation", H),
 Paragraph("Synthetic test article for pipeline checks. All data are fictional. DOI: 10.0000/test.solar.001", C),
 Paragraph("Abstract", H2),
 Paragraph("We report substrate-type Sb2Se3 solar cells with copper doping. The champion device doped with 1% Cu reached a power conversion efficiency (PCE) of 6.42%, "
           "an open-circuit voltage (Voc) of 0.468 V, a short-circuit current density (Jsc) of 28.9 mA/cm2 and a fill factor (FF) of 47.5%. "
           "The undoped control device showed a PCE of 5.70%.", B),
 Paragraph("1. Introduction", H2),
 Paragraph("Sb2Se3 is a promising absorber with a band gap of about 1.15 eV. The highest reported PCE for Sb2Se3 cells is 10.0% [5]. "
           "In this work we study the influence of Cu doping on device performance.", B),
 Paragraph("2. Experimental", H2),
 Paragraph("Devices were fabricated in the superstrate configuration on rigid FTO glass: FTO / CdS (60 nm) / Sb2Se3 (1.2 um) / Au (80 nm). "
           "CdS was deposited by chemical bath deposition. Sb2Se3 films were grown by close-spaced sublimation at 480 C for 10 min in vacuum. "
           "Cu was introduced by thermal evaporation of a thin Cu layer (1% and 2% atomic) before annealing. "
           "The active area of each device was 0.09 cm2 defined by a metal mask. "
           "Current density-voltage (J-V) curves were measured under AM1.5G illumination, 100 mW/cm2, in reverse scan from 0.6 V to -0.1 V at 0.1 V/s. "
           "The band gap determined from the Tauc plot was 1.15 eV.", B),
 Paragraph("3. Results and Discussion", H2),
 Paragraph("Table 1 summarizes the photovoltaic parameters. Values are given as mean of 20 devices with the standard deviation; the best device is shown in the last column.", B),
 Spacer(1, 4),
 table([["Device", "Voc (V)", "Jsc (mA/cm2)", "FF (%)", "PCE mean (%)", "PCE best (%)"],
        ["Control", "0.420", "26.1", "52.0", "5.52 ± 0.11", "5.70"],
        ["Cu 1%", "0.468", "28.9", "47.5", "6.20 ± 0.15", "6.42"],
        ["Cu 2%", "0.452", "27.8", "49.0", "6.95 ± 0.20", "7.10"]], [70, 60, 85, 55, 90, 80]),
 Paragraph("Table 1. Photovoltaic parameters of Sb2Se3 solar cells (AM1.5G, 100 mW/cm2).", C),
 Spacer(1, 6),
 Paragraph("The Cu 1% device exhibited the best Jsc of 28.9 mA/cm2 from J-V measurements, while the integrated EQE current of the same device was 27.1 mA/cm2. "
           "The control device reached a Voc of 0.420 V. Efficiency increased from 5.70% to 6.42% after doping with 1% Cu.", B),
 Paragraph("Stability. Unencapsulated devices stored in air retained 92% of their initial PCE after 500 h (ISOS-D-1).", B),
 Paragraph("4. Conclusion", H2),
 Paragraph("Cu doping improved the efficiency of Sb2Se3 solar cells to 6.42%.", B),
]
build("TEST_solar_Sb2Se3_synthetic.pdf", solar)

# ---------- 2. RAG ----------
rag = [
 Paragraph("Hybrid Retrieval with Reranking for Biomedical Question Answering", H),
 Paragraph("Synthetic test article for pipeline checks. All data are fictional. DOI: 10.0000/test.rag.001", C),
 Paragraph("Abstract", H2),
 Paragraph("We present a retrieval-augmented generation (RAG) system for biomedical question answering. The system combines BM25 and dense retrieval, "
           "reranks candidates with a cross-encoder and generates answers with Llama-3-8B-Instruct. On PubMedQA our hybrid configuration reaches Recall@5 of 0.81 "
           "and an exact match of 41.2%, outperforming the BM25 baseline.", B),
 Paragraph("1. System", H2),
 Paragraph("Documents are split into chunks of 512 tokens with an overlap of 64 tokens. Chunks are embedded with bge-base-en-v1.5 and stored in a FAISS index. "
           "Retrieval is hybrid (BM25 + dense). The top_k = 5 passages are reranked with bge-reranker-base and passed to the generator "
           "Llama-3-8B-Instruct in a zero-shot prompt; the retrieved context is concatenated in rank order.", B),
 Paragraph("2. Evaluation", H2),
 Paragraph("We evaluate on the PubMedQA test split (question answering, English) using 1,000 questions. All experiments ran on a single NVIDIA A100 GPU. "
           "Exact match (EM) is computed after normalization; token-level F1 follows the SQuAD protocol.", B),
 Spacer(1, 4),
 table([["Metric", "BM25 baseline", "Hybrid + rerank"],
        ["Recall@5", "0.70", "0.81"],
        ["nDCG@10", "0.55", "0.64"],
        ["EM (%)", "35.8", "41.2"],
        ["F1 (token-level)", "0.52", "0.58"],
        ["Accuracy (%)", "62.1", "104.3"]], [150, 110, 120]),
 Paragraph("Table 1. Results on PubMedQA (test split, 1,000 questions).", C),
 Spacer(1, 6),
 Paragraph("The mean latency of the hybrid pipeline was 420 ms per query, with 1,850 tokens per query on average. "
           "Prior work [3] reports an EM of 38.0% for a dense-only retriever on the same task.", B),
 Paragraph("3. Conclusion", H2),
 Paragraph("Hybrid retrieval with reranking improves Recall@5 from 0.70 to 0.81 and EM from 35.8% to 41.2%.", B),
]
build("TEST_rag_biomedical_synthetic.pdf", rag)
print("ok")
