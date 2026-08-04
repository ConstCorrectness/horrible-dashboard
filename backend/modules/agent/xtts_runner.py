import sys
import os
import io
import torch
import soundfile as sf
import struct
import json

from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts

def main():
    model_dir = os.path.expanduser("~/.local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2")
    config = XttsConfig()
    config.load_json(os.path.join(model_dir, "config.json"))
    model = Xtts.init_from_config(config)
    model.load_checkpoint(config, checkpoint_dir=model_dir, eval=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)
    
    speakers_file = os.path.join(model_dir, "speakers_xtts.pth")
    speaker_embeddings = torch.load(speakers_file, map_location="cpu", weights_only=False)

    print("READY", flush=True)

    # Read from stdin, write to stdout
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        text = line.strip()
        if not text:
            continue
        
        speaker = "Claribel Dervla"
        
        gpt_cond_latent = speaker_embeddings[speaker]["gpt_cond_latent"]
        speaker_embedding = speaker_embeddings[speaker]["speaker_embedding"]
        
        out = model.inference(
            text=text,
            language="en",
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
        )
        
        buf = io.BytesIO()
        sf.write(buf, out["wav"], 24000, format='WAV')
        audio_data = buf.getvalue()
        
        # Write size as 4 bytes little-endian, then data
        sys.stdout.buffer.write(struct.pack('<I', len(audio_data)))
        sys.stdout.buffer.write(audio_data)
        sys.stdout.buffer.flush()

if __name__ == "__main__":
    main()
