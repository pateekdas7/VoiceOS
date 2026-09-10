# X4-B — Cross-precision replay: T4 BF16 vs T4 FP32 on same T4 hardware.
# READ-ONLY forensic; no production kernel/CPU/.env/git/Twilio touched.
# Same seed=42, same sampler (temp=0.4, top_p=0.9, rep_pen=1.05), same corpus as X4-A.
# ONLY experimental variable: inference precision.
import json, os, sys, time, hashlib, subprocess, gc
print('=== X4B START', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))

import torch, transformers, numpy as np

# ---- Ensure snac ----
try:
    import snac
except Exception:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', 'snac==1.0.0', 'huggingface_hub', 'accelerate'])
    import snac

# ---- Env snapshot function (called once per precision run) ----
def env_snapshot(tag):
    return {
        'tag': tag,
        'torch': torch.__version__,
        'cuda': torch.version.cuda,
        'cudnn': torch.backends.cudnn.version(),
        'transformers': transformers.__version__,
        'snac': getattr(snac, '__version__', 'unknown'),
        'gpu_name': torch.cuda.get_device_name(0),
        'gpu_cap': torch.cuda.get_device_capability(0),
        'gpu_count': torch.cuda.device_count(),
        'bf16_supported': torch.cuda.is_bf16_supported(),
        'tf32_matmul': torch.backends.cuda.matmul.allow_tf32,
        'tf32_cudnn': torch.backends.cudnn.allow_tf32,
        'matmul_precision': torch.get_float32_matmul_precision(),
        'deterministic': torch.are_deterministic_algorithms_enabled(),
        'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }

# ---- Constants ----
START_OF_HUMAN=128259; END_OF_HUMAN=128260; START_OF_AI=128261; END_OF_AI=128262
START_OF_SPEECH=128257; END_OF_SPEECH=128258
AUDIO_BASE=128266; CODEBOOK_SIZE=4096; TOKENS_PER_FRAME=7
SNAC_MIN=AUDIO_BASE; SNAC_MAX=AUDIO_BASE + TOKENS_PER_FRAME*CODEBOOK_SIZE - 1
SR=24000

CORPUS = [
    ('drift',  'Aapke bank se transfer complete ho gaya hai.'),
    ('drift',  'Sir kya aap UPI se payment karna prefer karenge?'),
    ('drift',  'Total outstanding 24,568 rupees hai as of aaj.'),
    ('drift',  'Principal amount 15,000 rupees baaki hai.'),
    ('stable', 'Namaste sir, main Kavya bol rahi hoon Rajat Finance se.'),
    ('stable', 'Namaste madam, main Kavya bol rahi hoon.'),
    ('stable', 'Namaste sir aap kaise hain aaj?'),
    ('stable', 'Dhanyavaad sir, aapke response ka intezaar rahega.'),
    ('stable', 'Dhanyavaad, aapki payment successful ho gayi hai.'),
    ('stable', 'Sir, aapke account par 12,500 rupees ka outstanding hai.'),
    ('stable', 'Aap kaise hain?'),
    ('stable', 'Kripa karke wait karein.'),
    ('english','Good morning, this is a test message.'),
    ('english','Your account balance is one thousand five hundred rupees.'),
    ('num_heavy','9,876,543 rupees ka total amount pending hai.'),
    ('min_pair','Total outstanding hai as of aaj.'),
    ('min_pair','Total outstanding amount check kar rahi hoon.'),
    ('min_pair','Aapke bank se successful transaction huwa hai.'),
    ('min_pair','Bank transfer ho chuka hai sir, dhyaan dijiye.'),
]

SPEAKERS = ['kavya','apsara','agastya','vinaya','maitri','charu','ishana','kyra','mohini','varun','soumya']

# ---- Tokenizer (shared across precisions) ----
from transformers import AutoModelForCausalLM, AutoTokenizer
print('Loading tokenizer...')
tokenizer = AutoTokenizer.from_pretrained('maya-research/Veena')

# ---- SNAC (shared decoder; keep FP32 default) ----
from snac import SNAC
from huggingface_hub import hf_hub_download
import types as _types

print('Loading SNAC + patches...')
snac_cfg_path = hf_hub_download(repo_id='hubertsiuzdak/snac_24khz', filename='config.json')
snac_wts_path = hf_hub_download(repo_id='hubertsiuzdak/snac_24khz', filename='pytorch_model.bin')
with open(snac_cfg_path) as f: snac_cfg = json.load(f)
snac_model = SNAC(**snac_cfg)
def _strip_attn(seq):
    return torch.nn.Sequential(*[m for m in seq.children() if type(m).__name__ != 'LocalMHA'])
snac_model.encoder.block = _strip_attn(snac_model.encoder.block)
snac_model.decoder.model = _strip_attn(snac_model.decoder.model)
snac_state = torch.load(snac_wts_path, map_location='cpu', weights_only=False)
snac_model.load_state_dict(snac_state); snac_model.eval(); snac_model = snac_model.to('cpu')  # X4B: on CPU so FP32 model has full 2xT4 VRAM
def _snac_decode_compat(self, codes):
    z_q = 0
    for quantizer, code in zip(self.quantizer.quantizers, codes):
        z_q_i = quantizer.decode_code(code)
        z_q_i = quantizer.out_proj(z_q_i)
        if quantizer.stride > 1:
            z_q_i = z_q_i.repeat_interleave(quantizer.stride, dim=-1)
        z_q = z_q + z_q_i
    return self.decoder(z_q)
snac_model.decode = _types.MethodType(_snac_decode_compat, snac_model)
import snac.layers as _snac_layers
def _snake_plain(x, alpha):
    shape = x.shape
    x = x.reshape(shape[0], shape[1], -1)
    x = x + (alpha + 1e-9).reciprocal() * torch.sin(alpha * x).pow(2)
    x = x.reshape(shape)
    return x
_snac_layers.snake = _snake_plain
print('SNAC ready.')

# ---- F0 + decode helpers (identical to X4-A) ----
def f0_frame(frame, sr=SR, fmin=70, fmax=400):
    frame = frame.astype(np.float32) - frame.mean()
    if np.sqrt((frame*frame).mean()) < 300: return 0.0
    corr = np.correlate(frame, frame, mode='full'); corr = corr[len(corr)//2:]
    corr = corr / (corr[0] + 1e-9)
    lag_min = sr // fmax; lag_max = sr // fmin
    seg = corr[lag_min:lag_max]
    if len(seg)==0: return 0.0
    peak = int(np.argmax(seg)) + lag_min
    if corr[peak] < 0.30: return 0.0
    return sr / peak

def analyze(pcm_i16):
    n = len(pcm_i16); WIN=480; HOP=240
    if n < WIN: return {'class':'silent','median_f0':None,'p05_f0':None,'f0':[]}
    nf = 1 + (n - WIN)//HOP
    x = pcm_i16.astype(np.float32)
    f0s=[]; f0_traj=[]
    for i in range(nf):
        f = f0_frame(x[i*HOP:i*HOP+WIN])
        f0_traj.append(round(f,1))
        if f>0: f0s.append(f)
    if not f0s: return {'class':'silent','median_f0':None,'p05_f0':None,'f0':f0_traj}
    a=np.array(f0s); med=float(np.median(a)); p05=float(np.percentile(a,5))
    cls = 'female-kavya' if (med>=180 and p05>=140) else ('male-drift' if (med<165 or p05<120) else 'ambiguous')
    return {'class':cls,'median_f0':med,'p05_f0':p05,'f0':f0_traj}

def decode_snac_audio(audio_ids_int):
    if len(audio_ids_int) < 7: return np.zeros(0, dtype=np.int16)
    n_frames = len(audio_ids_int) // 7
    audio_ids_int = audio_ids_int[:n_frames*7]
    codes_l0=[]; codes_l1=[]; codes_l2=[]
    for f in range(n_frames):
        b = f*7
        codes_l0.append(audio_ids_int[b+0])
        codes_l1.append(audio_ids_int[b+1]); codes_l1.append(audio_ids_int[b+4])
        codes_l2.append(audio_ids_int[b+2]); codes_l2.append(audio_ids_int[b+3])
        codes_l2.append(audio_ids_int[b+5]); codes_l2.append(audio_ids_int[b+6])
    with torch.no_grad():
        c0 = torch.tensor([codes_l0], device='cpu', dtype=torch.long)
        c1 = torch.tensor([codes_l1], device='cpu', dtype=torch.long)
        c2 = torch.tensor([codes_l2], device='cpu', dtype=torch.long)
        wav = snac_model.decode([c0, c1, c2]).squeeze().float().cpu().numpy()
    pcm = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16)
    return pcm

def audio_ids_from_generated(seq_ids, prompt_len):
    tail = seq_ids[prompt_len:]
    aud=[]; pos=0
    for tid in tail:
        if SNAC_MIN <= tid <= SNAC_MAX:
            p = pos % TOKENS_PER_FRAME
            v = tid - AUDIO_BASE - p * CODEBOOK_SIZE
            aud.append(max(0, min(v, CODEBOOK_SIZE-1)))
            pos += 1
    return aud

def build_input(text, device='cuda', speaker='kavya'):
    prompt = f'<spk_{speaker}> {text}'
    pids = tokenizer.encode(prompt, add_special_tokens=False)
    ids = [START_OF_HUMAN, *pids, END_OF_HUMAN, START_OF_AI, START_OF_SPEECH]
    return torch.tensor([ids], device=device), ids

# ---- Core capture ----
def capture_generation(model, text, seed=42, max_new=None, device='cuda'):
    torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
    input_ids, ids_list = build_input(text, device=device)
    prompt_len = input_ids.shape[1]
    if max_new is None:
        max_new = min(int(len(text)*1.3) * TOKENS_PER_FRAME + 21, 700)
    with torch.no_grad():
        out = model.generate(
            input_ids, max_new_tokens=max_new, do_sample=True,
            temperature=0.4, top_p=0.9, repetition_penalty=1.05,
            pad_token_id=(tokenizer.pad_token_id if tokenizer.pad_token_id is not None else END_OF_SPEECH),
            eos_token_id=[END_OF_SPEECH, END_OF_AI],
            output_scores=True, return_dict_in_generate=True,
        )
    seq = out.sequences[0].tolist()
    gen_tail = seq[prompt_len:]
    scores = out.scores
    per_pos = []; K=5
    for i, s in enumerate(scores):
        logits = s[0].float()
        probs = torch.softmax(logits, dim=-1)
        topv, topi = torch.topk(logits, K)
        chosen = int(gen_tail[i]) if i < len(gen_tail) else -1
        top1_logit = float(topv[0].item()); top2_logit = float(topv[1].item())
        margin = top1_logit - top2_logit
        chosen_rank = -1
        for r, tid in enumerate(topi.tolist()):
            if tid == chosen: chosen_rank = r; break
        per_pos.append({
            'pos': i, 'chosen': chosen, 'chosen_rank_in_top5': chosen_rank,
            'chosen_prob': float(probs[chosen].item()) if 0 <= chosen < probs.shape[0] else None,
            'top5_ids': [int(x) for x in topi.tolist()],
            'top5_logits': [float(x) for x in topv.tolist()],
            'top5_probs': [float(probs[int(t)].item()) for t in topi.tolist()],
            'top1_top2_margin': margin,
            'is_audio': (SNAC_MIN <= chosen <= SNAC_MAX),
            'codebook_pos': None, 'codebook_val': None,
        })
        if SNAC_MIN <= chosen <= SNAC_MAX:
            audio_count = sum(1 for p in per_pos if p['is_audio'])
            cb_pos = (audio_count-1) % TOKENS_PER_FRAME
            cb_val = chosen - AUDIO_BASE - cb_pos * CODEBOOK_SIZE
            per_pos[-1]['codebook_pos'] = cb_pos
            per_pos[-1]['codebook_val'] = max(0, min(cb_val, CODEBOOK_SIZE-1))
    return {'seq': seq, 'prompt_len': prompt_len, 'per_pos': per_pos}

# ---- Speaker embed analysis ----
def spk_embed_analysis(model):
    spk_ids = {s: tokenizer.encode(f'<spk_{s}>', add_special_tokens=False) for s in SPEAKERS}
    emb = model.get_input_embeddings().weight.detach()
    out = {'ids': spk_ids, 'embed_dtype': str(emb.dtype), 'norms': {}, 'cos_to_kavya': {}}
    for s in SPEAKERS:
        if len(spk_ids[s]) != 1: out['norms'][s] = None; continue
        v = emb[spk_ids[s][0]].float()
        out['norms'][s] = float(v.norm().item())
    kv = emb[spk_ids['kavya'][0]].float(); kv_n = kv / (kv.norm() + 1e-9)
    for s in SPEAKERS:
        if len(spk_ids[s]) != 1: continue
        v = emb[spk_ids[s][0]].float(); v_n = v / (v.norm() + 1e-9)
        out['cos_to_kavya'][s] = float((kv_n * v_n).sum().item())
    return out

# ---- Full run for one precision ----
def run_precision(dtype, tag, device_map, force_matmul_high=True, max_memory=None):
    print(f'\n{"="*70}\n=== {tag}: dtype={dtype} device_map={device_map} ===\n{"="*70}')
    # Precision knobs
    if force_matmul_high:
        torch.set_float32_matmul_precision('highest')
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = True  # matches production
    env = env_snapshot(tag)
    print('ENV:', json.dumps(env, indent=2))

    t0 = time.time()
    kwargs = {'torch_dtype': dtype, 'device_map': device_map}
    if max_memory is not None: kwargs['max_memory'] = max_memory
    print(f'from_pretrained kwargs: {kwargs}')
    model = AutoModelForCausalLM.from_pretrained('maya-research/Veena', **kwargs)
    model.eval()
    load_s = time.time() - t0
    env['load_seconds'] = load_s
    env['model_config_dtype'] = str(model.dtype)
    env['model_num_params'] = sum(p.numel() for p in model.parameters())
    env['model_commit_hash'] = getattr(model.config, '_commit_hash', None)
    env['attn_impl'] = str(getattr(model.config, '_attn_implementation', None))
    # Precision verification
    dtypes = {}
    for n, p in list(model.named_parameters()):
        dt = str(p.dtype)
        dtypes[dt] = dtypes.get(dt, 0) + 1
    env['param_dtype_counts'] = dtypes
    # Sample specific layer dtypes
    sample_layers = {}
    for name in ['model.embed_tokens.weight', 'model.norm.weight', 'lm_head.weight']:
        for n, p in model.named_parameters():
            if n == name:
                sample_layers[n] = {'dtype': str(p.dtype), 'device': str(p.device), 'shape': list(p.shape)}
                break
    env['sample_layer_dtypes'] = sample_layers
    # Also snapshot device placement across GPUs
    dev_counts = {}
    for n, p in model.named_parameters():
        d = str(p.device); dev_counts[d] = dev_counts.get(d, 0) + 1
    env['param_device_counts'] = dev_counts
    print(f'load={load_s:.1f}s dtype={model.dtype} params={env["model_num_params"]:,}')
    print(f'param_dtype_counts: {dtypes}')
    print(f'param_device_counts: {dev_counts}')
    print(f'sample_layer_dtypes: {json.dumps(sample_layers, indent=2)}')

    # Speaker embed analysis
    spk_a = spk_embed_analysis(model)
    print(f'embed_dtype: {spk_a["embed_dtype"]}')
    print(f'cos_to_kavya: {json.dumps(spk_a["cos_to_kavya"], indent=2)}')

    # Main corpus
    records = []
    for idx, (bucket, text) in enumerate(CORPUS):
        tt = time.time()
        cap = capture_generation(model, text, seed=42)
        latency = time.time() - tt
        aud_vals = audio_ids_from_generated(cap['seq'], cap['prompt_len'])
        pcm = decode_snac_audio(aud_vals) if len(aud_vals) >= 21 else np.zeros(0, dtype=np.int16)
        ana = analyze(pcm)
        first_21 = [p for p in cap['per_pos'] if p['is_audio']][:21]
        rec = {
            'idx': idx, 'bucket': bucket, 'text': text,
            'prompt_len': cap['prompt_len'], 'n_gen': len(cap['per_pos']),
            'n_audio': sum(1 for p in cap['per_pos'] if p['is_audio']),
            'first_21_audio_positions': first_21,
            'audio_ids_full': aud_vals,
            'audio_sha16': hashlib.sha256(bytes(pcm)).hexdigest()[:16],
            'audio_seconds': len(pcm)/SR,
            'class': ana['class'], 'median_f0': ana['median_f0'], 'p05_f0': ana['p05_f0'],
            'latency_s': latency,
        }
        records.append(rec)
        first21_ids = [p['chosen'] for p in first_21]
        print(f'[{tag}][{idx:2d}][{bucket:9s}] cls={ana["class"]:12s} med={ana["median_f0"]} p05={ana["p05_f0"]} n_aud={rec["n_audio"]:3d} sha={rec["audio_sha16"]} lat={latency:.1f}s first5={first21_ids[:5]}')

    # Determinism (3 reps × critical texts)
    DET_IDX = [2, 3, 15]   # male minimal-pair, female minimal-pair, second male minimal-pair
    det = []
    for di in DET_IDX:
        text = CORPUS[di][1]; reps=[]
        for r in range(3):
            cap = capture_generation(model, text, seed=42)
            aud_vals = audio_ids_from_generated(cap['seq'], cap['prompt_len'])
            pcm = decode_snac_audio(aud_vals) if len(aud_vals)>=21 else np.zeros(0, dtype=np.int16)
            ana = analyze(pcm)
            f21 = [p['chosen'] for p in cap['per_pos'] if p['is_audio']][:21]
            reps.append({'rep': r, 'audio_sha16': hashlib.sha256(bytes(pcm)).hexdigest()[:16],
                         'first21_audio_ids': f21, 'class': ana['class'],
                         'median_f0': ana['median_f0'], 'p05_f0': ana['p05_f0']})
        det.append({'idx': di, 'text': text,
                    'unique_sha16': len({r['audio_sha16'] for r in reps}),
                    'unique_first21_audio': len({tuple(r['first21_audio_ids']) for r in reps}),
                    'reps': reps})
        print(f'[{tag}][det][{di}] unique_sha={det[-1]["unique_sha16"]} unique_first21={det[-1]["unique_first21_audio"]} classes={[r["class"] for r in reps]}')

    # Free memory
    del model
    gc.collect(); torch.cuda.empty_cache()

    return {'env': env, 'spk_embed_analysis': spk_a, 'records': records, 'determinism': det}

# ---- TEST A: BF16 baseline (reproduce X4-A) ----
try:
    TEST_A = run_precision(torch.bfloat16, 'T4_BF16', device_map='cuda')
except Exception as e:
    print(f'TEST A FAILED: {e}')
    import traceback; traceback.print_exc()
    TEST_A = {'error': str(e)}

# Aggressive cleanup before FP32 load
gc.collect(); torch.cuda.empty_cache()
print(f'Pre-FP32 GPU mem: cuda:0={torch.cuda.memory_allocated(0)/1e9:.2f}GB free={torch.cuda.mem_get_info(0)[0]/1e9:.2f}GB | cuda:1={torch.cuda.memory_allocated(1)/1e9:.2f}GB free={torch.cuda.mem_get_info(1)[0]/1e9:.2f}GB')

# ---- TEST B: FP32 on same T4×2 (device_map='auto' + max_memory to split evenly) ----
try:
    TEST_B = run_precision(torch.float32, 'T4_FP32', device_map='auto', max_memory={0: '13GiB', 1: '13GiB', 'cpu': '20GiB'})
except Exception as e:
    print(f'TEST B FAILED: {e}')
    import traceback; traceback.print_exc()
    TEST_B = {'error': str(e)}

# ---- TEST C: not available (Kaggle T4×2 only per session constraints) ----
TEST_C = {
    'status': 'NOT_AVAILABLE',
    'reason': 'Only Kaggle T4x2 GPU available in this session; no SM80+ (L4/A100/H100) provisioned. Per hard rule, no new production environment was provisioned to run this comparison.'
}

# ---- Save ----
OUT = '/kaggle/working/x4b_results.json'
with open(OUT, 'w') as f:
    json.dump({
        'corpus': CORPUS,
        'test_a_bf16': TEST_A,
        'test_b_fp32': TEST_B,
        'test_c_sm80': TEST_C,
    }, f, indent=1, default=lambda o: o.tolist() if hasattr(o, 'tolist') else str(o))
print(f'\nWROTE {OUT}, size={os.path.getsize(OUT)} bytes')
print('=== X4B DONE', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))