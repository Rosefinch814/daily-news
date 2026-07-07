from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "cards"
HTML = ROOT / "cards.html"


slides = [
    {
        "layout": "pure",
        "kicker": "",
        "title": "<span class='hi'>AI 新闻</span><br>刷半天<br>重点还是会漏",
        "subtitle": "费时低效，还怕重要消息<br>看到晚了。",
        "chips": [],
        "note": "左滑看解决办法 →",
    },
    {
        "layout": "trust",
        "kicker": "我最怕这个",
        "title": "事实和观点<br>混着写",
        "subtitle": "新闻事实先交代，AI 判断单独标出来。",
        "image": "raw/mobile-03.png",
        "note": "事实摘要 + AI 分析分区",
    },
    {
        "layout": "newspaper",
        "kicker": "不是信息流",
        "title": "每天一份<br>中文科技报",
        "subtitle": "不是无限下滑。每天一期，打开就有头条和速览。",
        "image": "raw/mobile-01.png",
        "note": "报头 / 日期 / 头条精读",
    },
    {
        "layout": "reader",
        "kicker": "读者定位",
        "title": "小白能看懂<br>圈内能扫重点",
        "subtitle": "少一点术语堆砌，多一点背景、影响和边界。",
        "image": "raw/mobile-04.png",
        "note": "背景 / 影响 / 判断",
    },
    {
        "layout": "split",
        "kicker": "阅读路径",
        "title": "先读头条<br>再扫速览",
        "subtitle": "重要的讲透，其他的用一分钟跟上。",
        "image": "raw/mobile-02.png",
        "image2": "raw/mobile-brief-02.png",
        "note": "头条精读 + 速览",
    },
    {
        "layout": "closing",
        "kicker": "少刷一点",
        "title": "想跟上 AI<br>不用天天刷屏",
        "subtitle": "从这份中文科技日报开始。",
        "image": "raw/mobile-01.png",
        "note": "Tourbillion.News / Technology",
    },
]


CSS = """
:root{
  --paper:#f7f0e4;
  --paper2:#eee4d0;
  --ink:#171310;
  --ink2:#3b332c;
  --muted:#766d61;
  --rule:#cfc2aa;
  --seal:#a9342b;
  --seal2:#872820;
  --safe:72px;
}
*{box-sizing:border-box}
body{
  margin:0;
  background:#d8ceb9;
  color:var(--ink);
  font-family:"PingFang SC","Noto Serif SC","Songti SC",serif;
}
.stage{display:grid;gap:48px;padding:48px}
.card{
  position:relative;
  width:1080px;
  height:1440px;
  overflow:hidden;
  isolation:isolate;
  background:
    radial-gradient(rgba(72,43,24,.032) 1px,transparent 1.1px) 0 0/7px 7px,
    linear-gradient(180deg,#fbf5ea 0%,#eee3d0 100%);
  box-shadow:0 40px 100px rgba(21,15,8,.18);
}
.card:before{
  content:"";
  position:absolute;
  inset:0;
  border-top:18px solid var(--seal);
  z-index:20;
  pointer-events:none;
}
.grain{
  position:absolute;
  inset:0;
  background:radial-gradient(rgba(166,52,43,.045) 1px,transparent 1.5px) 0 0/18px 18px;
  opacity:.55;
  mix-blend-mode:multiply;
  pointer-events:none;
  z-index:19;
}
.copy{
  position:absolute;
  left:var(--safe);
  right:var(--safe);
  top:76px;
  z-index:8;
}
.pure .copy{
  top:86px;
  left:60px;
  right:46px;
  bottom:auto;
  display:block;
  transform:none;
  text-align:left;
}
.kicker{
  display:inline-flex;
  align-items:center;
  gap:14px;
  margin:0 0 22px;
  color:var(--seal);
  font:800 24px/1.1 "PingFang SC","Noto Serif SC",serif;
  letter-spacing:.05em;
}
.kicker:before{
  content:"";
  width:42px;
  height:6px;
  background:var(--seal);
}
h1{
  margin:0;
  max-width:850px;
  color:var(--ink);
  font:900 82px/1.04 "PingFang SC","Noto Serif SC","Songti SC",serif;
  letter-spacing:0;
}
.subtitle{
  max-width:850px;
  margin-top:24px;
  color:var(--ink2);
  font:700 34px/1.45 "PingFang SC","Noto Serif SC",serif;
}
.note{
  position:absolute;
  z-index:10;
  display:inline-flex;
  align-items:center;
  gap:12px;
  min-height:50px;
  padding:11px 18px;
  background:rgba(251,245,234,.92);
  border:1px solid rgba(169,52,43,.26);
  color:var(--seal2);
  font:800 23px/1.1 "PingFang SC","Noto Serif SC",serif;
  letter-spacing:.02em;
  backdrop-filter:blur(8px);
}
.note:before{
  content:"";
  width:10px;
  height:10px;
  background:var(--seal);
  flex:none;
}
.phone{
  position:absolute;
  overflow:hidden;
  background:var(--paper);
  border:1px solid rgba(26,23,20,.16);
  border-radius:38px;
  box-shadow:0 30px 72px rgba(38,28,16,.18),0 0 0 10px rgba(255,255,255,.44);
  z-index:5;
}
.phone img{
  display:block;
  width:100%;
  height:100%;
  object-fit:cover;
}
.phone:after{
  content:"";
  position:absolute;
  top:14px;
  left:50%;
  width:118px;
  height:10px;
  transform:translateX(-50%);
  border-radius:999px;
  background:rgba(26,23,20,.16);
}

.pure h1{
  max-width:980px;
  margin:0;
  color:var(--ink);
  font-size:116px;
  line-height:1.14;
  text-shadow:0 1px 0 rgba(255,255,255,.6);
}
.pure h1 .hi{
  position:relative;
  display:inline-block;
  padding:0 14px 6px;
  color:var(--seal2);
}
.pure h1 .hi:before{
  content:"";
  position:absolute;
  left:0;
  right:0;
  bottom:15px;
  height:.4em;
  background:rgba(169,52,43,.17);
  z-index:-1;
}
.pure .subtitle{
  display:block;
  position:absolute;
  left:0;
  top:560px;
  width:960px;
  max-width:960px;
  margin:0;
  padding:0;
  background:transparent;
  border-left:0;
  color:var(--ink);
  font-size:68px;
  line-height:1.28;
  font-weight:900;
}
.pure .kicker{
  display:none;
}
.pure .chips{
  display:none;
}
.pure .chip{
  width:max-content;
  padding:11px 18px 13px;
  border:0;
  background:linear-gradient(transparent 48%,rgba(169,52,43,.18) 48%);
  color:#2d2823;
  font:900 34px/1.08 "PingFang SC","Noto Serif SC",serif;
}
.pure .badge{
  display:none;
}
.pure .note{
  left:60px;
  top:auto;
  bottom:auto;
  transform:none;
  top:1048px;
  display:inline-flex;
  min-height:58px;
  padding:14px 22px;
  background:rgba(169,52,43,.1);
  border:0;
  color:var(--seal2);
  font-size:31px;
  font-weight:850;
}
.pure .giant{
  display:none;
}

.trust .copy,.newspaper .copy,.reader .copy,.split .copy,.closing .copy{top:76px}
.trust .phone{left:82px;bottom:64px;width:560px;height:858px}
.trust .phone img{object-position:50% 24%}
.trust .note{right:72px;top:548px}
.trust .label{
  position:absolute;
  right:76px;
  z-index:9;
  width:300px;
  color:#2d2823;
  font:850 28px/1.28 "PingFang SC","Noto Serif SC",serif;
}
.trust .label small{
  display:block;
  margin-top:8px;
  color:var(--muted);
  font:650 21px/1.35 "PingFang SC","Noto Serif SC",serif;
}
.trust .label:before{
  content:"";
  display:block;
  width:72px;
  height:4px;
  margin-bottom:18px;
  background:var(--seal);
}
.trust .l1{top:690px}
.trust .l2{top:854px}

.newspaper .phone{
  left:150px;
  right:150px;
  bottom:82px;
  height:780px;
  border-radius:34px;
}
.newspaper .phone img{object-position:50% 0}
.newspaper .note{left:150px;top:552px}

.reader .phone{right:70px;bottom:72px;width:592px;height:930px}
.reader .phone img{object-position:50% 13%}
.reader .note{left:72px;bottom:118px}
.reader .side{
  position:absolute;
  left:72px;
  bottom:236px;
  z-index:8;
  width:275px;
  color:#342e27;
  font:850 32px/1.35 "PingFang SC","Noto Serif SC",serif;
}
.reader .side span{
  display:block;
  margin-bottom:14px;
  color:var(--seal);
  font-size:24px;
  letter-spacing:.08em;
}

.split .phone{
  bottom:78px;
  width:420px;
  height:830px;
  border-radius:32px;
}
.split .phone.a{left:72px}
.split .phone.b{right:72px}
.split .phone.a img{object-position:50% 8%}
.split .phone.b img{object-position:50% 0}
.split .note{left:72px;top:548px}

.closing .phone{
  left:214px;
  bottom:72px;
  width:650px;
  height:900px;
  border-radius:38px;
}
.closing .phone img{object-position:50% 0}
.closing .note{left:72px;bottom:92px}
.closing .wash{
  position:absolute;
  inset:0;
  z-index:7;
  pointer-events:none;
  background:linear-gradient(180deg,rgba(251,245,234,0) 48%,rgba(251,245,234,.78) 86%);
}
"""


def write_html() -> None:
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<style>{CSS}</style></head><body><div class='stage'>",
    ]
    for idx, slide in enumerate(slides, 1):
        layout = slide["layout"]
        parts.append(f"<section id='card-{idx}' class='card {layout}'>")
        parts.append("<div class='grain'></div>")
        parts.append("<div class='copy'>")
        parts.append(f"<div class='kicker'>{slide['kicker']}</div>")
        parts.append(f"<h1>{slide['title']}</h1>")
        parts.append(f"<div class='subtitle'>{slide['subtitle']}</div>")
        parts.append("</div>")

        if layout == "split":
            parts.append(f"<div class='phone a'><img src='{slide['image']}'></div>")
            parts.append(f"<div class='phone b'><img src='{slide['image2']}'></div>")
        elif "image" in slide:
            parts.append(f"<div class='phone'><img src='{slide['image']}'></div>")

        parts.append(f"<div class='note'>{slide['note']}</div>")

        if layout == "pure":
            parts.append("<div class='giant'>AI</div>")
            parts.append("<div class='badge'>先看这一份</div>")
            parts.append("<div class='chips'>")
            for chip in slide["chips"]:
                parts.append(f"<div class='chip'>{chip}</div>")
            parts.append("</div>")
        elif layout == "trust":
            parts.append("<div class='label l1'>事实先写清楚<small>原文里确定发生了什么</small></div>")
            parts.append("<div class='label l2'>分析单独标出来<small>影响判断不冒充事实</small></div>")
        elif layout == "reader":
            parts.append("<div class='side'><span>不用懂术语</span>也不把复杂问题写扁</div>")
        elif layout == "closing":
            parts.append("<div class='wash'></div>")

        parts.append("</section>")

    parts.append("</div></body></html>")
    HTML.write_text("\n".join(parts), encoding="utf-8")


def render() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1180, "height": 1540}, device_scale_factor=1)
        page.goto(HTML.resolve().as_uri(), wait_until="networkidle")
        for idx in range(1, len(slides) + 1):
            page.locator(f"#card-{idx}").screenshot(path=str(OUT / f"{idx:02d}.png"))
        browser.close()


if __name__ == "__main__":
    write_html()
    render()
