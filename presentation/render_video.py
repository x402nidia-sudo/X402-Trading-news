from PIL import Image,ImageDraw,ImageFont
from pathlib import Path
import json,math,subprocess,textwrap

OUT=Path(__file__).resolve().parent
FINAL=OUT/'Trading_News_Agent_v4_EN.mp4'
W,H,FPS=1280,720,24
BG='#101910';PANEL='#1c281f';BORDER='#34463a';FG='#f3f5ea';MUTED='#afc0b0';LIME='#c6f56b';ORANGE='#e5b783'
REG='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
MONO='/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
font=lambda n,b=False:ImageFont.truetype(BOLD if b else REG,n)
mono=lambda n:ImageFont.truetype(MONO,n)
scenes=[(7, 'TRADING NEWS', 'The story that matters\nfor your asset.', 'A Python agent for people and other agents.', 'intro', 'Trading News selects relevant crypto news with explainable ranking and pay-per-use USDC payments on Algorand.'), (8, '01 / INSTALLATION', 'Download. Install. Query.', 'No manual configuration-file edits.', 'install', 'Extract the ZIP and open INSTALL.bat on Windows or install.sh on Linux. The installer adds the dependencies and writes config.txt.'), (9, '02 / SETUP', 'A guided setup, step by step.', 'No news API keys or paid AI API required.', 'wizard', 'The installer checks the service, connects the wallet, asks for a budget and verifies the balance. Setup cannot finish while required information is missing.'), (10, '03 / PERSONAL USE', 'Pera approves. The agent continues.', 'Scan the QR code and approve the transactions on your phone.', 'pera', 'A local browser window connects Pera. If your wallet needs preparation for USDC, the installer requests approval and displays the reserve and fee before signing.'), (8, '04 / CHOOSE AN ASSET', '49 symbols available.', 'The catalog preserves the assets in the original project.', 'symbols', 'Open START.bat or start.sh. The agent lists the supported symbols and asks which asset you want to query, such as ETH.'), (9, '05 / TWO PURCHASES', '0.10 + 0.10 = 0.20 USDC', 'Two resources and two receipts, within a per-query limit.', 'payments', 'The first purchase obtains the feed. The second selects a story. Both are project service charges, not a fixed ten-cent Algorand or contest fee.'), (9, '06 / NEWS SELECTION', 'The highest-ranked story, explained.', 'Relevance · recency · source · event type', 'ranking', 'The service filters by asset, groups duplicates and reduces the priority of speculation. It returns the highest-ranked story among the available results and explains its choice.'), (8, '07 / THE RESULT', 'One story. Its source. Its context.', 'Follow the source link to check the original content.', 'result', 'The response includes a headline, link, available timestamp, reasons and alternatives. GDELT detection is not presented as a verified publication date.'), (9, '08 / OTHER AGENTS', 'The same query, returned as JSON.', 'A discoverable catalog and a simple Python entry point.', 'agent', 'Another agent can list symbols and request a query with --json --pay, or import get_news. Unattended mode uses a dedicated local wallet and spending limits.'), (8, '09 / RECOVERY', 'Resume without paying twice.', 'The order reference preserves the purchase and its receipts.', 'recovery', 'If a response is lost, --resume recovers the original purchase. If valid fresh news is missing at preflight, the agent does not request a signature or charge.'), (8, '10 / VALIDATION', 'Tested locally. Deployment pending.', 'A real Pera purchase must be checked after deployment.', 'status', 'The package includes local tests of both payments and their recovery. The new backend still needs deployment and validation of a real purchase from Pera.'), (7, 'TRADING NEWS / ALGORAND x402', 'From a symbol\nto a selected news story.', 'Python · GDELT · USDC · Pera · x402', 'end', 'Trading News is a Python agent for asset-specific news with explainable selection, pay-per-use payments and guided setup.')]
TOTAL=sum(s[0] for s in scenes)

def wrap(draw,text,x,y,width,f,fill=FG,gap=9):
    for paragraph in text.split('\n'):
        words=paragraph.split();line=''
        for word in words:
            attempt=(line+' '+word).strip()
            if draw.textlength(attempt,font=f)>width and line:
                draw.text((x,y),line,font=f,fill=fill);y+=f.size+gap;line=word
            else:line=attempt
        draw.text((x,y),line,font=f,fill=fill);y+=f.size+gap
    return y

def box(d,rect,fill=PANEL):d.rounded_rectangle(rect,18,fill=fill,outline=BORDER,width=1)
def badge(d,x,y,text,color=LIME):
    f=font(15,True);w=d.textlength(text,font=f)+32
    d.rounded_rectangle((x,y,x+w,y+35),17,fill=color);d.text((x+16,y+8),text,font=f,fill=BG)

def terminal(d,lines,x=72,y=294,width=1136,height=277):
    box(d,(x,y,x+width,y+height),'#0b110c')
    for j,col in enumerate(('#da7c72','#d4b974','#82b98a')):d.ellipse((x+20+20*j,y+17,x+29+20*j,y+26),fill=col)
    d.text((x+100,y+11),'trading_news_agent',font=mono(15),fill=MUTED)
    py=y+58
    for text,color,size in lines:
        d.text((x+26,py),text,font=mono(size),fill=color);py+=size+16

def base(i,s):
    dur,k,title,subtitle,kind,caption=s
    im=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(im)
    d.rounded_rectangle((52,30,92,70),12,fill=LIME);d.text((60,32),'tn',font=font(25,True),fill=BG)
    d.text((108,41),'tradingnews',font=font(21,True),fill=FG)
    d.text((930,44),'PYTHON AGENT / v4',font=mono(16),fill=MUTED)
    d.line((52,92,1228,92),fill=BORDER,width=1)
    d.text((72,120),k,font=font(14,True),fill=LIME)
    titlefont=font(48 if kind in ('intro','end') else 36,True)
    y=wrap(d,title,72,157,1140,titlefont,gap=4)
    d.text((72,y+11),subtitle,font=font(20),fill=MUTED)
    if kind=='intro':
        for j,(big,small) in enumerate([('49','ASSETS'),('2','WAYS TO USE'),('0.20','USDC / QUERY')]):
            x=72+j*385;box(d,(x,390,x+365,544));d.text((x+25,410),big,font=font(55,True),fill=LIME);d.text((x+25,495),small,font=font(15,True),fill=MUTED)
    elif kind=='install':
        for j,(n,a,b) in enumerate([('1','GitHub / ZIP','Download and extract the folder'),('2','INSTALL.bat','Windows: open the installer'),('3','config.txt','Written automatically')]):
            x=72+j*385;box(d,(x,310,x+365,565));badge(d,x+24,335,n);d.text((x+24,408),a,font=font(26,True),fill=FG);wrap(d,b,x+24,465,316,font(18),MUTED)
    elif kind=='wizard':
        terminal(d,[('TRADING NEWS · Guided setup',LIME,22),('1/4  Checking the service',FG,20),('2/4  Pera or a dedicated wallet',FG,20),('3/4  Daily limit [1.00 USDC]',FG,20),('4/4  Wallet readiness and balance',FG,20)],height=302)
    elif kind=='pera':
        box(d,(72,305,784,578));d.text((101,334),'Connect Pera',font=font(29,True),fill=LIME)
        for j,text in enumerate(['1. Open the local agent window.','2. Connect Pera on your phone.','3. Review and approve each payment.']):d.text((101,399+j*46),text,font=font(22),fill=FG)
        box(d,(888,294,1138,592),'#25332a');d.rounded_rectangle((903,308,1123,577),25,fill='#111b13');d.rounded_rectangle((972,319,1054,331),5,fill=BORDER)
        d.text((950,365),'PERA',font=font(30,True),fill=LIME);d.text((946,430),'0.10 USDC',font=font(23,True),fill=FG)
        d.rounded_rectangle((928,501,1098,547),10,fill=LIME);d.text((957,513),'Approve',font=font(21,True),fill=BG)
    elif kind=='symbols':
        terminal(d,[('$ python agent.py',LIME,22),('BTC   ETH   BNB   XRP   ADA   SOL   DOGE   ALGO',FG,22),('... full catalog of 49 symbols ...',MUTED,19),('Which symbol would you like to query? ETH',FG,23),('Maximum per query: 0.20 USDC',LIME,21)],height=292)
    elif kind=='payments':
        for x,title,price,path,desc in [(72,'1 / NEWS FEED','0.10','/api/v1/market-signal/ETH','Relevant news for the selected asset'),(667,'2 / SELECTION','0.10','/api/v1/news/ETH/best','Selected story, reasons and alternatives')]:
            box(d,(x,310,x+541,577));d.text((x+25,335),title,font=font(17,True),fill=MUTED);d.text((x+25,374),price,font=font(61,True),fill=LIME);d.text((x+222,404),'USDC',font=font(24),fill=FG);d.text((x+25,480),path,font=mono(18),fill=FG);d.text((x+25,531),desc,font=font(18),fill=MUTED)
    elif kind=='ranking':
        items=[('Headlines unrelated to ETH','Discarded',.17,MUTED),('Speculative price prediction','Lower priority',.43,ORANGE),('Material event affecting Ethereum','Higher priority',.90,LIME)]
        for j,(left,right,v,col) in enumerate(items):
            y=307+j*89;box(d,(72,y,1208,y+76));d.text((94,y+17),left,font=font(21),fill=FG);d.text((795,y+17),right,font=font(19,True),fill=col);d.rounded_rectangle((94,y+54,94+int(1040*v),y+59),3,fill=col)
    elif kind=='result':
        box(d,(72,302,1208,584));badge(d,98,324,'ETH · FICTIONAL EXAMPLE')
        wrap(d,'Ethereum blockchain critical vulnerability fixed in security upgrade',98,383,1060,font(28,True),FG,5)
        d.text((98,471),'Source and link: b.example/security · test data',font=font(18),fill=MUTED)
        d.text((98,513),'Reasons: ETH relevance · recency · security event',font=font(18),fill=FG)
        d.text((98,550),'Timestamp: GDELT detection; publication unverified',font=font(16),fill=MUTED)
    elif kind=='agent':
        terminal(d,[('$ python agent.py --symbol ETH --json --pay',LIME,22),('{',FG,20),('  "best_article": { "title": "…", "url": "…" },',FG,20),('  "total_charged_usdc": "0.2", "order_id": "…"',FG,20),('}',FG,20)],height=292)
    elif kind=='recovery':
        terminal(d,[('$ python agent.py --pending --json',MUTED,21),('$ python agent.py --resume ORDER_ID --json --pay',LIME,21),('The original purchase and receipts are preserved.',FG,21),('No valid news at preflight: no payment.',FG,21)],height=281)
    elif kind=='status':
        rows=[('54 local checks','Signatures, simulated payments, selection and recovery'),('Next: deploy the backend','Update the API on the existing Render service'),('Then: validate with Pera','A real 0.20 USDC query and verification of both receipts')]
        for j,(a,b) in enumerate(rows):
            y=307+j*87;box(d,(72,y,1208,y+73));d.text((94,y+12),a,font=font(22,True),fill=LIME if j==0 else FG);d.text((94,y+43),b,font=font(16),fill=MUTED)
    elif kind=='end':
        badge(d,72,399,'NO NEWS OR AI API KEYS')
        wrap(d,'x402nidia-sudo / X402-Trading-news',72,470,1140,font(31,True),FG)
        d.text((72,532),'README + installer + agent + automatic configuration',font=font(21),fill=MUTED)
    d.line((52,628,1228,628),fill=BORDER)
    d.text((72,647),'VISUAL EXPLAINER · No real funds transferred in this video',font=font(14),fill=MUTED)
    d.text((1140,645),f'{i+1:02d} / 12',font=mono(16),fill=MUTED)
    return im

bases=[]
for i,s in enumerate(scenes):
    b=base(i,s);b.save(OUT/f'scene_{i:02d}.png');bases.append(b)

def stamp(seconds):
    ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'

srt=[];cursor=0
for i,s in enumerate(scenes):
    srt.append(f'{i+1}\n{stamp(cursor)} --> {stamp(cursor+s[0]-.1)}\n'+s[5]+'\n');cursor+=s[0]
(OUT/'Trading_News_Agent_v4_EN.srt').write_text('\n'.join(srt))
(OUT/'script_en.json').write_text(json.dumps([{'duration':s[0],'title':s[2],'caption':s[5]} for s in scenes],ensure_ascii=False,indent=2))
# A silent visual explainer: captions and the interface carry the message.
cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','pipe:0','-i',str(OUT/'Trading_News_Agent_v4_EN.srt'),'-map','0:v','-map','1:0','-c:v','libx264','-preset','fast','-crf','21','-pix_fmt','yuv420p','-c:s','mov_text','-metadata:s:s:0','language=eng','-metadata','title=Trading News Agent · Python y Algorand x402','-movflags','+faststart',str(FINAL)]
p=subprocess.Popen(cmd,stdin=subprocess.PIPE)
elapsed=0
try:
 for i,(s,im) in enumerate(zip(scenes,bases)):
    for n in range(s[0]*FPS):
        # Short crossfade and a timeline make navigation and changes explicit.
        if i and n<8:frame=Image.blend(bases[i-1],im,(n+1)/8)
        else:frame=im.copy()
        d=ImageDraw.Draw(frame);progress=(elapsed+n/FPS)/TOTAL
        d.rectangle((0,710,int(W*progress),719),fill=LIME)
        p.stdin.write(frame.tobytes())
    elapsed+=s[0];print(f'Scene {i+1}/12 ready',flush=True)
finally:p.stdin.close()
if p.wait():raise SystemExit('ffmpeg failed')
print(f'Video: {TOTAL} seconds · {FINAL}',flush=True)
