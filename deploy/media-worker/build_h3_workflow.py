"""Extract the pinned native H3 I2V standard branch; no optional Turbo or embeddings."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
ui = json.loads((root / 'h3-ui.json').read_text(encoding='utf-8'))
upstream = {n['id']: n for n in ui['definitions']['subgraphs'][0]['nodes']}
graph = {}


def node(i, kind, **inputs):
    graph[str(i)] = {'class_type': kind, 'inputs': inputs}


node(6, 'UNETLoader', unet_name=upstream[6]['widgets_values'][0], weight_dtype='default')
node(13, 'CLIPLoader', clip_name=upstream[13]['widgets_values'][0], type='minimax', device='default')
node(11, 'VAELoader', vae_name=upstream[11]['widgets_values'][0])
node(24, 'VAELoader', vae_name=upstream[24]['widgets_values'][0])
node(114, 'LoadImage', image='h3-first-frame.png')
node(104, 'MiniMaxH3ImageToVideo', clip=['13', 0], vae=['11', 0],
     prompt='A single continuous shot based on the first frame. Gentle natural motion and slow camera movement. Audio: subtle ambient sound. No dialogue, no subtitles.',
     width=864, height=480, length=124, first_frame=['114', 0])
node(15, 'RandomNoise', noise_seed=42)
node(16, 'BasicGuider', model=['6', 0], conditioning=['104', 0])
node(17, 'KSamplerSelect', sampler_name=upstream[17]['widgets_values'][0])
node(9, 'BasicScheduler', model=['6', 0], scheduler='simple', steps=upstream[124]['widgets_values'][0], denoise=1.0)
node(14, 'SamplerCustomAdvanced', noise=['15', 0], guider=['16', 0], sampler=['17', 0],
     sigmas=['9', 0], latent_image=['104', 1])
node(10, 'VAEDecode', samples=['14', 0], vae=['11', 0])
node(23, 'VAEDecodeAudio', samples=['14', 0], vae=['24', 0])
node(91, 'CreateVideo', images=['10', 0], audio=['23', 0], fps=24.0)
node(92, 'SaveVideo', video=['91', 0], filename_prefix='arcreel/h3', format='mp4',
     **{'format.codec': 'h264', 'format.codec.encoding': 'auto'})
for n in graph.values():
    for v in n['inputs'].values():
        if isinstance(v, list):
            assert v[0] in graph
assert graph['91']['inputs']['audio'] == ['23', 0]
assert graph['104']['inputs']['length'] % 17 == 5
(root / 'h3-api.json').write_text(json.dumps(graph, indent=2), encoding='utf-8')
print('H3 workflow:', len(graph), 'nodes;', graph['9']['inputs']['steps'], 'steps; 124 frames at 24 fps')
