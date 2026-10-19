import asyncio, re, sys, pathlib
from playwright.async_api import async_playwright
import pdfplumber

ROOT = pathlib.Path(__file__).parent
SRC = ROOT / "report.html"
OUT = ROOT / "SE-2405_Bolatov_ProbPeriod1.pdf"

HEADINGS = [
    ("s1", "1. Project proposal"), ("s2", "2. Evidence of relevance"), ("s3", "3. Literature review"),
    ("s4", "4. Existing system comparison"), ("s5", "5. Technology comparison"),
    ("s6", "6. Requirements specification"), ("s7", "7. Architecture and diagrams"),
    ("s8", "8. UX / prototype"), ("s9", "9. Verification and evaluation plan"),
    ("s10", "10. Project management and risks"), ("s11", "11. Supervisor feedback"),
    ("s12", "12. References"), ("sA", "Appendix A. Links to materials and survey screenshots"),
]

FOOTER = """<div style="font-family:Inter,sans-serif;font-size:8pt;color:#666;width:100%;padding:0 18mm;display:flex;justify-content:space-between">
<span>SE-2405 · Bolatov B. A. · public.safe · Probation period 1</span><span class="pageNumber"></span></div>"""

def toc_html(pages):
    items = []
    for key, title in HEADINGS:
        items.append(f'<li><span>{title}</span><span>{pages.get(key, "")}</span></li>')
    return "\n".join(items)

async def render(html, out):
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        await pg.goto(SRC.with_name("_tmp.html").as_uri())
        await pg.wait_for_load_state("networkidle")
        await pg.pdf(path=str(out), format="A4", print_background=True,
                     display_header_footer=True, header_template="<span></span>", footer_template=FOOTER,
                     margin={"top": "20mm", "bottom": "20mm", "left": "18mm", "right": "18mm"})
        await b.close()

def find_pages(pdf):
    pages = {}
    with pdfplumber.open(pdf) as doc:
        for i, page in enumerate(doc.pages, 1):
            if i <= 2: continue
            text = page.extract_text() or ""
            lines = [l.strip() for l in text.splitlines()]
            for key, title in HEADINGS:
                if key in pages: continue
                if any(l.startswith(title[:28]) for l in lines):
                    pages[key] = i
    return pages

async def main():
    base = SRC.read_text()
    tmp = SRC.with_name("_tmp.html")
    tmp.write_text(base.replace("<!-- generated -->", toc_html({})))
    await render(base, OUT)
    pages = find_pages(OUT)
    print("pages:", pages)
    tmp.write_text(base.replace("<!-- generated -->", toc_html(pages)))
    await render(base, OUT)
    pages2 = find_pages(OUT)
    assert pages == pages2, (pages, pages2)
    with pdfplumber.open(OUT) as doc:
        print("total pages:", len(doc.pages))

asyncio.run(main())
