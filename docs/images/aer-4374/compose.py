"""Stack each device strip (device/<n>-*.png) above its twin strip
(twin/<n>.png) into one labelled image. Usage: compose.py <work_dir> <out_dir>"""
import sys
from PIL import Image, ImageDraw, ImageFont
S=sys.argv[1]; out=sys.argv[2]
rows=["0-tab_section.png","1-tab_section-left.png","2-button.png"]
names=["tab_section_top","tab_section_left","button"]
import os
try:
  font=ImageFont.truetype(os.environ.get("CAPTION_FONT","DejaVuSans-Bold.ttf"),14)
except OSError:
  font=ImageFont.load_default()
for k,(dev,name) in enumerate(zip(rows,names)):
  d=Image.open(f"{S}/device/{dev}").convert("RGB")
  t=Image.open(f"{S}/twin/{k}.png").convert("RGB")
  lab=70; W=lab+max(d.width,t.width); H=d.height+t.height+8
  img=Image.new("RGB",(W,H),"white"); dr=ImageDraw.Draw(img)
  img.paste(d,(lab,0)); img.paste(t,(lab,d.height+8))
  dr.text((6,d.height//2),"device",fill="black",font=font); dr.text((6,d.height+8+t.height//2),"twin",fill="black",font=font)
  img.save(f"{out}/{name}.png"); print(name, img.size, d.size, t.size)
