// A KV cache of f32 (see kv_f16.wgsl): the exact path, for comparing with
// llama.cpp run with an f32 cache.
alias KV4 = vec4<f32>;

fn kv4(x: KV4) -> vec4<f32> {
  return x;
}
