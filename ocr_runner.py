import sys, json
sys.stdout.reconfigure(encoding='utf-8')
from paddleocr import PaddleOCR

ocr = PaddleOCR(lang="ch")
paths = json.loads(sys.argv[1])
results = {}
for p in paths:
    try:
        r = ocr.ocr(p)
        if r and r[0]:
            lines = []
            for line in r[0]:
                box = line[0]
                text = line[1][0]
                x = min(pt[0] for pt in box)
                y = min(pt[1] for pt in box)
                w = max(pt[0] for pt in box) - x
                h = max(pt[1] for pt in box) - y
                is_vertical = h > w * 1.5
                if is_vertical:
                    lines.append((x, -y, text, 'vertical'))
                else:
                    lines.append((y, x, text, 'horizontal'))
            
            vertical_lines = [l for l in lines if l[3] == 'vertical']
            horizontal_lines = [l for l in lines if l[3] == 'horizontal']
            
            if vertical_lines and len(vertical_lines) > len(horizontal_lines):
                vertical_lines.sort(key=lambda l: (-l[0], l[1]))
                sorted_text = "\n".join(l[2] for l in vertical_lines)
            else:
                horizontal_lines.sort(key=lambda l: (l[0], l[1]))
                sorted_text = "\n".join(l[2] for l in horizontal_lines)
            
            results[p] = sorted_text
        else:
            results[p] = ""
    except Exception as e:
        results[p] = ""
print(json.dumps(results, ensure_ascii=False))
