#!/usr/bin/env python3
"""Full pipeline latency test: greeting TTFA + full turn (VAD+STT+LLM+TTS) latency."""
import asyncio, base64, json, math, time
import websockets

WS_URL = "ws://127.0.0.1:8010/twilio/ws"
STREAM_SID = "MX_fulltest_002"
CALL_SID   = "CA_fulltest_002"
ACCOUNT_SID = "ACtest"

def gen_mulaw_silence(n=160):
    return bytes([0x7f] * n)

def gen_mulaw_speech(n=160):
    out = []
    for i in range(n):
        v = int(32767 * math.sin(2 * math.pi * 400 * i / 8000))
        v = max(-32768, min(32767, v))
        sign = 0x80 if v < 0 else 0
        if v < 0:
            v = -v
        v = min(v + 132, 32767)
        exp = 7
        for e in range(7):
            if v <= (0xFF << (e + 3)):
                exp = e
                break
        out.append(~(sign | (exp << 4) | ((v >> (exp + 3)) & 0xF)) & 0xFF)
    return bytes(out)

async def run():
    print(f"Connecting to {WS_URL} ...")
    try:
        async with websockets.connect(WS_URL) as ws:
            await ws.send(json.dumps({
                "event": "connected", "protocol": "Call", "version": "1.0.0"
            }))
            await ws.send(json.dumps({
                "event": "start", "sequenceNumber": "1", "streamSid": STREAM_SID,
                "start": {
                    "streamSid": STREAM_SID, "accountSid": ACCOUNT_SID, "callSid": CALL_SID,
                    "tracks": ["inbound"],
                    "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                    "customParameters": {"customer_id": "CUST-PRATEEK-001"}
                }
            }))
            print("Sent start handshake, waiting for greeting...")

            chunks = 0
            t0 = time.time()
            ttfa = None
            while time.time() - t0 < 15:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 0.5))
                    if m.get("event") == "media":
                        chunks += 1
                        if ttfa is None:
                            ttfa = time.time() - t0
                            print(f"GREETING TTFA: {ttfa*1000:.0f}ms")
                    elif m.get("event") == "mark":
                        print(f"Mark: {m.get('mark',{}).get('name','')}")
                        if chunks > 5:
                            break
                except asyncio.TimeoutError:
                    if chunks > 30:
                        break
            print(f"Greeting done: {chunks} chunks in {time.time()-t0:.1f}s\n")

            # 200ms silence (settle VAD)
            seq = 2
            for _ in range(10):
                payload = base64.b64encode(gen_mulaw_silence()).decode()
                await ws.send(json.dumps({
                    "event": "media", "sequenceNumber": str(seq), "streamSid": STREAM_SID,
                    "media": {"track": "inbound", "chunk": str(seq),
                              "timestamp": str(seq * 20), "payload": payload}
                }))
                seq += 1
                await asyncio.sleep(0.02)

            # 2s of synthetic speech
            print("Sending 2s speech (400Hz sine as mu-law)...")
            for _ in range(100):
                payload = base64.b64encode(gen_mulaw_speech()).decode()
                await ws.send(json.dumps({
                    "event": "media", "sequenceNumber": str(seq), "streamSid": STREAM_SID,
                    "media": {"track": "inbound", "chunk": str(seq),
                              "timestamp": str(seq * 20), "payload": payload}
                }))
                seq += 1
                await asyncio.sleep(0.02)

            # 1.2s trailing silence -> triggers VAD end-of-speech
            print("Trailing silence (EOS trigger)...")
            for _ in range(60):
                payload = base64.b64encode(gen_mulaw_silence()).decode()
                await ws.send(json.dumps({
                    "event": "media", "sequenceNumber": str(seq), "streamSid": STREAM_SID,
                    "media": {"track": "inbound", "chunk": str(seq),
                              "timestamp": str(seq * 20), "payload": payload}
                }))
                seq += 1
                await asyncio.sleep(0.02)

            eos = time.time()
            rc = 0
            rttfa = None
            print("Listening for response audio (STT -> LLM -> TTS)...")
            while time.time() - eos < 12:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 0.3))
                    if m.get("event") == "media":
                        rc += 1
                        if rttfa is None:
                            rttfa = time.time() - eos
                            print(f"TURN TTFA: {rttfa*1000:.0f}ms from EOS")
                    elif m.get("event") == "mark":
                        print(f"Mark: {m.get('mark',{}).get('name','')}")
                except asyncio.TimeoutError:
                    if rc > 10:
                        break

            print(f"\n=== RESULTS ===")
            print(f"Greeting TTFA:  {ttfa*1000:.0f}ms" if ttfa else "Greeting TTFA:  NONE (NO AUDIO)")
            print(f"Turn TTFA:      {rttfa*1000:.0f}ms" if rttfa else "Turn TTFA:      NONE (no response audio)")
            print(f"Response chunks:{rc}")
            if rttfa:
                status = "PASS" if rttfa < 1.5 else "FAIL"
                print(f"Target <1500ms: {status}")
    except Exception as e:
        print(f"ERROR: {e}")
        raise

asyncio.run(run())
