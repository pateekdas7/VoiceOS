#!/usr/bin/env python3
"""Intra-datacenter latency validation (CPU node → GPU node, uses requests)."""
from __future__ import annotations
import argparse, base64, json, struct, sys, time

try:
    import requests
except ImportError:
    print("ERROR: pip install requests", file=sys.stderr); sys.exit(1)

_TRANSCRIPTS = [
    "mera bakaya kitna hai", "main abhi paise nahi de sakta",
    "mujhe kiston mein bhugtan karna hai", "meri tankha nahi aayi hai",
    "kya settlement ho sakta hai", "mujhe ek mahine ka samay chahiye",
    "main das hazar de sakta hoon", "aap mujhe pareshan kyun kar rahe hain",
    "theek hai main kal paise dunga", "mera khata number kya hai",
    "byaaj kitna hai", "kya mujhe rasid milegi",
    "main online payment kar sakta hoon", "mujhe nahi pata tha itna bakaya hai",
    "kya aap mujhe EMI de sakte hain", "main paanch hazar aaj de sakta hoon",
    "mujhe RBI guidelines pata hain", "theek hai main samajh gaya",
    "Haan bhai, theek hai payment karte hain", "Arey yaar, thoda time do mujhe",
]
_SIL = ""

def _sil():
    global _SIL
    if not _SIL:
        c = 32000
        _SIL = base64.b64encode(struct.pack(f"<{c}h", *([0]*c))).decode()
    return _SIL

def _pct(d, p):
    if not d: return 0.0
    s = sorted(d); return s[max(0, int(p*len(s))-1)]

def stt(sess, url):
    t = time.perf_counter()
    sess.post(f"{url}/transcribe", json={"audio_b64":_sil(),"language":"hi","beam_size":1}, timeout=30).raise_for_status()
    return (time.perf_counter()-t)*1000

def llm(sess, url, text):
    p={"model":"qwen2.5-7b-instruct-fp8","messages":[{"role":"system","content":"Reply briefly in Hindi."},{"role":"user","content":text}],"stream":True,"max_tokens":48}
    t=time.perf_counter(); ms=0.0
    with sess.post(f"{url}/v1/chat/completions", json=p, stream=True, timeout=30) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line: continue
            line=(line.decode() if isinstance(line,bytes) else line).strip()
            if line.startswith("data:") and line!="data: [DONE]":
                try:
                    d=json.loads(line[5:]).get("choices",[{}])[0].get("delta",{}).get("content","")
                    if d: ms=(time.perf_counter()-t)*1000; break
                except: pass
    return ms or (time.perf_counter()-t)*1000

def tts(sess, url, text):
    t=time.perf_counter(); ms=0.0
    with sess.post(f"{url}/synthesize", json={"text":text,"speaker":"kavya"}, stream=True, timeout=120) as r:
        r.raise_for_status()
        for chunk in r.iter_content(8192):
            if chunk and not ms: ms=(time.perf_counter()-t)*1000
    return ms or (time.perf_counter()-t)*1000

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--gpu-host",required=True); ap.add_argument("--calls",type=int,default=100); ap.add_argument("--skip-stt",action="store_true"); args=ap.parse_args()
    su=f"http://{args.gpu_host}:8100"; lu=f"http://{args.gpu_host}:8000"; tu=f"http://{args.gpu_host}:8200"
    print(f"INTRA-DC ({args.calls} calls): CPU 101.53.137.131 → GPU {args.gpu_host}\n")
    stts,llms,ttss,fas,errs=[],[],[],[],[]
    sess=requests.Session()
    try:
        sess.get(f"{su}/health/ready",timeout=5).raise_for_status()
        sess.get(f"{tu}/health/ready",timeout=5).raise_for_status()
        sess.get(f"{lu}/health",timeout=5).raise_for_status()
        print("Services healthy.\n")
    except Exception as e: print(f"ABORT: {e}",file=sys.stderr); return 1
    for i in range(args.calls):
        tx=_TRANSCRIPTS[i%len(_TRANSCRIPTS)]
        try:
            sm=stt(sess,su) if not args.skip_stt else 0.0
            if not args.skip_stt: stts.append(sm)
            lm=llm(sess,lu,tx); llms.append(lm)
            tm=tts(sess,tu,tx); ttss.append(tm)
            fa=sm+lm+tm; fas.append(fa)
            print(f"[{i+1:03d}/{args.calls}] STT={sm:.0f} LLM={lm:.0f} TTS={tm:.0f} fa={fa:.0f}ms '{tx[:32]}'")
        except Exception as ex:
            errs.append(f"{i+1}:{ex}"); print(f"[{i+1:03d}/{args.calls}] ERROR: {ex}")
            if not args.skip_stt: stts.append(9999.0)
            llms.append(9999.0); ttss.append(9999.0); fas.append(9999.0)
    print("\n"+"="*60)
    def st(lbl,d):
        if d: print(f"  {lbl:<22} p50={_pct(d,.5):.0f} p95={_pct(d,.95):.0f} p99={_pct(d,.99):.0f} min={min(d):.0f} max={max(d):.0f}")
    if not args.skip_stt: st("STT:",stts)
    st("LLM TTFT:",llms); st("TTS TTFA:",ttss); st("FIRST-AUDIO:",fas)
    p95=_pct(fas,.95); ok=p95<=1500
    print(f"\nGATE p95<=1500ms: {'PASS' if ok else 'FAIL'} — {p95:.0f}ms")
    if errs: print(f"ERRORS: {errs[:5]}")
    return 0 if ok else 1

if __name__=="__main__": sys.exit(main())
