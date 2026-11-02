"""Word ceiling is independent of page count; title/date are included."""
import tempfile
from pathlib import Path
import pdfplumber
from pypdf import PdfReader
import market_brief as m
manifest={'end':'2026-09-25T00:00:00Z'}
header=m.word_count(m.display_paragraphs({'paragraphs':[]},manifest))
assert m.word_count(['<b>Market</b> &amp; prices: $12.50 near-term'])==4
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp)/'brief.pdf'
 exact={'paragraphs':['word '*(480-header)]}
 assert m.layout_brief(exact,manifest)[1]['total_words']==480
 assert m.render(exact,manifest,p)
 over={'paragraphs':['word '*(481-header)]}
 assert not m.render(over,manifest,p)
 # Deliberately spread fewer than 480 words across pages: pagination must be allowed.
 spread={'paragraphs':['This is a short paragraph.']*60}
 assert m.render(spread,manifest,p)
 assert len(PdfReader(p).pages)>1
 with pdfplumber.open(p) as doc:
  for page in doc.pages:
   assert not page.annots
   assert {round(c['size'],2) for c in page.chars}=={12.0}
   assert all('Arial' in c['fontname'] for c in page.chars)
   assert min(c['x0'] for c in page.chars)>=35.99 and max(c['x1'] for c in page.chars)<=576.01
   assert min(c['top'] for c in page.chars)>=36 and max(c['bottom'] for c in page.chars)<=756
print('PASS: title/date count, 480 accepted, 481 rejected, multiple pages allowed, Arial 12 and half-inch margins')
