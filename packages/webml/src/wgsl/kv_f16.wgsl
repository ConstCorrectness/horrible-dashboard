// A KV cache of f16: four values in a vec2<u32>, two halves per word. Prepended
// to attn_prefill.wgsl, which reads the cache only through KV4 and kv4
// (attention.wgsl has its own views of it, in kernels.ts). Halves the cache's
// memory and what attention reads; arithmetic stays f32.
alias KV4 = vec2<u32>;

fn kv4(x: KV4) -> vec4<f32> {
  return vec4<f32>(unpack2x16float(x.x), unpack2x16float(x.y));
}
