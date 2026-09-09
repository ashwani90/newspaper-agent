"""Generate a realistic multi-column newspaper PDF for testing.

This exists so you can exercise the whole pipeline (and see what the output
looks like) before pointing it at a real e-paper.

    python scripts/make_sample_pdf.py inbox/sample-edition.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A3
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = A3
MARGIN = 15 * mm
GUTTER = 8 * mm
N_COLS = 4

MASTHEAD = "THE MORNING CHRONICLE"
DATELINE = "New Delhi  |  Tuesday, 12 March 2026  |  Rs 8  |  Vol XLII No 71"

ARTICLES = [
    # (page, section, headline, byline, paragraphs)
    (
        1, "FRONT PAGE",
        "RBI holds repo rate at 6.25% as inflation eases to 4.1%",
        "By Meera Raghavan, Economics Bureau",
        [
            "The Reserve Bank of India kept its benchmark repo rate unchanged at 6.25 per cent on Monday, ending a run of three consecutive cuts, after retail inflation cooled to a nine-month low of 4.1 per cent in February.",
            "Governor Sanjay Malhotra said the Monetary Policy Committee voted four to two in favour of a pause, describing the decision as a chance to let earlier cuts work their way through the economy before easing further.",
            "The central bank trimmed its GDP growth forecast for the coming fiscal year to 6.7 per cent from 6.9 per cent, citing weaker export demand and a softer monsoon outlook. It left its inflation projection at 4.4 per cent.",
            "Bond markets took the announcement calmly. The ten-year benchmark yield rose two basis points to 6.81 per cent, while the rupee closed at 87.42 to the dollar, little changed on the day.",
            "Economists were divided on the path ahead. A pause now buys the committee time, said one analyst at a Mumbai brokerage, but if food prices stay soft there is room for one more cut before the fiscal deficit target is reviewed in July.",
        ],
    ),
    (
        1, "FRONT PAGE",
        "Anthropic opens Bengaluru research office, plans 300 hires",
        "By Arjun Nair, Technology Correspondent",
        [
            "Artificial intelligence company Anthropic said it will open a research and engineering office in Bengaluru this year, its first in India, with plans to hire about 300 staff over eighteen months.",
            "The office will focus on model evaluation, safety research and enterprise deployment for customers in South and Southeast Asia, according to a statement. Roughly half the roles will be for machine learning engineers.",
            "The announcement lands amid a hiring scramble among large language model developers in India. OpenAI opened a New Delhi office last year, and several domestic startups have raised large rounds to build generative AI products for Indian languages.",
            "Industry bodies welcomed the move. India trains more engineers than anywhere else, said a NASSCOM spokesperson, and the country is finally capturing frontier research work rather than only support functions.",
        ],
    ),
    (
        2, "BUSINESS",
        "Fintech startup Kaviya raises $85 million Series C at $1.2 billion valuation",
        "By Priya Deshmukh",
        [
            "Bengaluru-based lending startup Kaviya has raised 85 million dollars in a Series C round led by an existing investor, valuing the six-year-old company at 1.2 billion dollars and making it the year's fourth Indian unicorn.",
            "The company, which lends to small merchants through partner banks, said it will use the capital to expand into tier-three cities and to build an underwriting model trained on transaction data rather than credit bureau scores.",
            "Venture capital funding for Indian fintech has recovered sharply from its 2023 trough, with 4.1 billion dollars deployed across 210 deals last year. Several late-stage companies are said to be preparing IPO filings.",
            "Kaviya reported revenue of 1,840 crore rupees and its first full year of profit in the year to March, according to filings.",
        ],
    ),
    (
        2, "BUSINESS",
        "Cloud spending by Indian banks to cross $3 billion as AWS, Azure compete",
        "By Staff Reporter",
        [
            "Indian banks and insurers will spend more than three billion dollars on public cloud services this year, a report published on Monday estimated, as regulatory comfort with offshore data processing grows.",
            "Amazon Web Services and Microsoft Azure together hold about seventy per cent of the market. Both have built dedicated regions in Mumbai and Hyderabad to satisfy data localisation rules.",
            "Cybersecurity remains the sticking point. A data breach at a mid-sized private bank in November exposed records of 1.4 million customers and prompted the regulator to tighten audit requirements for third-party cloud providers.",
        ],
    ),
    (
        2, "HEALTH",
        "Universal dengue vaccine clears mid-stage clinical trial",
        "By Dr Latha Krishnan",
        [
            "A dengue vaccine designed to protect against all four viral serotypes has cleared a mid-stage clinical trial in Pune and Chennai, showing 78 per cent efficacy across 4,200 participants, researchers reported.",
            "The World Health Organization has listed dengue among its top ten global health threats. India recorded roughly 290,000 cases last year, though surveillance experts believe the true figure is many times higher.",
            "The developers will seek drug approval after a larger phase three trial begins in September. If it succeeds, the vaccine could reach government immunisation programmes by 2029.",
        ],
    ),
    (
        3, "NATION",
        "Monsoon forecast trimmed as Indian Ocean warming accelerates",
        "By Environment Bureau",
        [
            "Forecasters lowered their prediction for this year's southwest monsoon to 94 per cent of the long-period average, warning that rapid warming in the Indian Ocean is disrupting the circulation patterns the season depends on.",
            "Climate change has made the monsoon more erratic rather than simply weaker, scientists said, with longer dry spells punctuated by extreme rainfall days. Emissions from coal generation remain the largest domestic driver.",
            "Renewable capacity additions hit a record 32 gigawatts last year, most of it solar, but coal still supplies about seventy per cent of generation. Air quality in the northern plains stayed in the severe band for 41 days this winter.",
        ],
    ),
    (
        3, "EDUCATION",
        "UGC clears four-year degree rollout at 340 more universities",
        "By Education Correspondent",
        [
            "The University Grants Commission has approved the four-year undergraduate degree structure at 340 additional universities from the coming academic session, extending a central plank of the National Education Policy.",
            "The revised curriculum allows multiple exit points and credit transfer between institutions. Vice-chancellors at several state universities said faculty shortages could slow implementation.",
            "Admission cycles will move to a common schedule from next year, a change the commission said would reduce the practice of students holding seats at several institutions at once.",
        ],
    ),
    (
        3, "WORLD",
        "Sanctions talks stall as trade war widens to semiconductors",
        "By Foreign Desk",
        [
            "Negotiations over easing export sanctions collapsed without agreement on Friday, and both delegations left the United Nations complex in Geneva blaming the other for the impasse.",
            "The dispute has widened from steel and agricultural tariffs to semiconductors, with new licensing requirements affecting chip manufacturing equipment. A treaty on technology transfer signed in 2024 is now effectively suspended.",
            "Diplomacy over the contested border corridor continues on a separate track. Officials described those talks as difficult but not deadlocked.",
        ],
    ),
]


def wrap(text: str, font_size: float, max_width: float, c: canvas.Canvas, font: str) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word])
        if c.stringWidth(trial, font, font_size) <= max_width and current:
            current.append(word)
        elif not current:
            current = [word]
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def draw_classifieds(c: canvas.Canvas) -> None:
    """Page 4: classifieds. Many very short lines, no prose."""
    top = PAGE_H - MARGIN
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(PAGE_W / 2, top - 20, "CLASSIFIEDS")
    c.setFont("Helvetica", 9)
    c.drawCentredString(PAGE_W / 2, top - 36, "Matrimonial  |  Situations Vacant  |  To Let  |  Public Notice")

    col_width = (PAGE_W - 2 * MARGIN - GUTTER * 3) / 4
    entries = [
        "MATRIMONIAL", "Alliance invited, 28/5'6\"", "MBA, Delhi. Box 4471",
        "Sikh girl 26, MSc, Ludhiana", "Contact 98110 22143",
        "SITUATIONS VACANT", "Accountant reqd, 3 yrs exp", "Salary 45000. Box 2210",
        "Drivers wanted, LMV licence", "Call 011 4455 6677",
        "Data entry, 12th pass", "Walk in 10am-4pm",
        "TO LET", "2BHK Lajpat Nagar 32000", "3BHK Dwarka Sec 12 41000",
        "Shop 400sqft Karol Bagh", "Godown 2000sqft Okhla",
        "FOR SALE", "Maruti Swift 2019 5.4L", "Royal Enfield 2021 1.8L",
        "Office furniture, bulk", "PUBLIC NOTICE",
        "Change of name: Ramesh Kumar", "to Ramesh Sharma, Delhi",
        "Lost and found: PAN card", "E-tender no 2026/DL/0447",
        "Auction notice, 14 March", "Terms and conditions apply",
    ]
    for col in range(4):
        y = top - 60
        for entry in entries[col * 7 : col * 7 + 7] * 3:
            if y < MARGIN:
                break
            bold = entry.isupper() and len(entry) < 22
            c.setFont("Helvetica-Bold" if bold else "Helvetica", 8.5 if bold else 7.5)
            c.drawString(MARGIN + col * (col_width + GUTTER), y, entry[:44])
            y -= 11
    c.showPage()


def draw_market_table(c: canvas.Canvas) -> None:
    """Page 5: market data. Mostly digits, almost no sentences."""
    top = PAGE_H - MARGIN
    c.setFont("Helvetica-Bold", 20)
    c.drawString(MARGIN, top - 18, "MARKET WATCH")
    c.setFont("Helvetica", 9)
    c.drawString(MARGIN, top - 34, "SENSEX 79412.55  +212.40   NIFTY 24118.90  +64.15   BSE  NSE")

    scrips = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "ITC",
        "BHARTIARTL", "LT", "AXISBANK", "KOTAKBANK", "MARUTI", "HINDUNILVR",
        "BAJFINANCE", "ASIANPAINT", "WIPRO", "TATAMOTORS", "SUNPHARMA",
        "TITAN", "ULTRACEMCO", "NESTLEIND", "POWERGRID", "NTPC", "ONGC",
    ]
    col_width = (PAGE_W - 2 * MARGIN - GUTTER * 3) / 4
    c.setFont("Helvetica-Bold", 7.5)
    for col in range(4):
        c.drawString(MARGIN + col * (col_width + GUTTER), top - 54,
                     "SCRIP        PREV CLOSE   HIGH   LOW   CLOSE")
    c.setFont("Helvetica", 7.5)
    for col in range(4):
        y = top - 66
        for i, scrip in enumerate(scrips):
            if y < MARGIN:
                break
            base = 400 + (i * 137 + col * 53) % 3200
            row = (f"{scrip:<12} {base:>8.2f} {base * 1.02:>7.2f} "
                   f"{base * 0.98:>7.2f} {base * 1.004:>7.2f}")
            c.drawString(MARGIN + col * (col_width + GUTTER), y, row)
            y -= 9.5
    c.setFont("Helvetica", 7.5)
    c.drawString(MARGIN, MARGIN + 14,
                 "52-week high low data. Gainers 1842 Losers 1391. "
                 "Exchange rate USD 87.42 EUR 94.18 GBP 110.65.")
    c.showPage()


def draw_full_page_ad(c: canvas.Canvas) -> None:
    """Page 6: a full-page advert. Very little text."""
    c.setFont("Helvetica-Bold", 64)
    c.drawCentredString(PAGE_W / 2, PAGE_H / 2 + 40, "MEGA SALE")
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(PAGE_W / 2, PAGE_H / 2 - 10, "UPTO 60% OFF")
    c.setFont("Helvetica", 14)
    c.drawCentredString(PAGE_W / 2, PAGE_H / 2 - 50, "All stores. Limited period.")
    c.setFont("Helvetica", 9)
    c.drawCentredString(PAGE_W / 2, MARGIN + 20, "Advertisement. Terms and conditions apply.")
    c.setLineWidth(3)
    c.rect(MARGIN, MARGIN, PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN)
    c.showPage()


def build(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out_path), pagesize=A3)

    col_width = (PAGE_W - 2 * MARGIN - GUTTER * (N_COLS - 1)) / N_COLS
    pages = sorted({a[0] for a in ARTICLES})

    for page_no in pages:
        top = PAGE_H - MARGIN

        if page_no == 1:
            c.setFont("Times-Bold", 44)
            c.drawCentredString(PAGE_W / 2, top - 36, MASTHEAD)
            c.setFont("Times-Roman", 11)
            c.drawCentredString(PAGE_W / 2, top - 56, DATELINE)
            c.setLineWidth(1.5)
            c.line(MARGIN, top - 68, PAGE_W - MARGIN, top - 68)
            content_top = top - 92
        else:
            c.setFont("Times-Bold", 12)
            c.drawString(MARGIN, top - 12, f"{MASTHEAD}  |  12 March 2026")
            c.drawRightString(PAGE_W - MARGIN, top - 12, f"PAGE {page_no}")
            c.setLineWidth(0.6)
            c.line(MARGIN, top - 20, PAGE_W - MARGIN, top - 20)
            content_top = top - 44

        col = 0
        y = content_top

        def col_x(index: int) -> float:
            return MARGIN + index * (col_width + GUTTER)

        def new_column() -> tuple[int, float]:
            return col + 1, content_top

        for art_page, section, headline, byline, paragraphs in ARTICLES:
            if art_page != page_no:
                continue

            needed = 60
            if y - needed < MARGIN:
                col, y = new_column()
            if col >= N_COLS:
                break

            c.setFont("Helvetica-Bold", 8)
            c.drawString(col_x(col), y, section)
            y -= 14

            for line in wrap(headline, 15, col_width, c, "Times-Bold"):
                if y < MARGIN + 20:
                    col, y = new_column()
                    if col >= N_COLS:
                        break
                c.setFont("Times-Bold", 15)
                c.drawString(col_x(col), y, line)
                y -= 17
            if col >= N_COLS:
                break

            y -= 3
            c.setFont("Times-Italic", 9)
            c.drawString(col_x(col), y, byline)
            y -= 14

            for para in paragraphs:
                for line in wrap(para, 9.5, col_width, c, "Times-Roman"):
                    if y < MARGIN + 12:
                        col, y = new_column()
                        if col >= N_COLS:
                            break
                    c.setFont("Times-Roman", 9.5)
                    c.drawString(col_x(col), y, line)
                    y -= 11.5
                if col >= N_COLS:
                    break
                y -= 5
            if col >= N_COLS:
                break

            y -= 10
            if y > MARGIN + 30:
                c.setLineWidth(0.4)
                c.line(col_x(col), y, col_x(col) + col_width, y)
                y -= 16

        c.showPage()

    # Pages 4-6 are the pages you would want to skip: classifieds, market
    # data, and a full-page advert. They exist so the page classifier and the
    # --pages selector can be tested against something realistic.
    draw_classifieds(c)
    draw_market_table(c)
    draw_full_page_ad(c)

    c.save()
    print(f"wrote {out_path} (6 pages: 1-3 articles, 4 classifieds, "
          f"5 market data, 6 advert)")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("inbox/sample-edition.pdf")
    build(target)
