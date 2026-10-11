'''STEP 6.c — production vLLMAdapter.generate_stream multi-delta proof.'''
import asyncio, os, sys, time
sys.path.insert(0, '/opt/voiceos/app')

async def main():
    from deployment.cpu.app import build_gpu_scheduler
    from src.services.llm_runtime.adapters.vllm_adapter import vLLMAdapter
    from src.services.llm_runtime.prompt_contract import PromptContract
    from src.services.conversation_engine.engine import _default_response_plan

    plan = _default_response_plan()
    print('plan.delivery.max_response_tokens =', plan.delivery.max_response_tokens)

    llm = vLLMAdapter(
        gpu_scheduler=build_gpu_scheduler(),
        prompt_contract=PromptContract(),
        base_url=os.environ['LLM_BASE_URL'],
    )
    print('adapter=vLLMAdapter base_url=', os.environ['LLM_BASE_URL'])

    prompt = 'नमस्ते! आप कैसे हैं? एक छोटा जवाब दें।'
    print('prompt:', prompt)

    t0 = time.monotonic()
    ttft = None
    n = 0
    total_text = []
    async for tok in await llm.generate_stream(prompt=prompt, response_plan=plan, max_tokens=32):
        if ttft is None:
            ttft = (time.monotonic()-t0)*1000
        n += 1
        total_text.append(tok.text)
        if n <= 12:
            print(f'  delta[{n-1}] ms={((time.monotonic()-t0)*1000):.0f} finish={tok.finish_reason!r} text={tok.text!r}')
    elapsed=(time.monotonic()-t0)*1000
    print(f'n_deltas={n} ttft_ms={ttft:.0f} wall_ms={elapsed:.0f}')
    print('assembled:', ''.join(total_text))

if __name__=='__main__':
    asyncio.run(main())
