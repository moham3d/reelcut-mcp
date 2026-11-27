"""`flow schema` - the machine-readable contract an agent can plan against.

Every command, its flags, its default, whether it can spend, and what it
returns. Kept honest by `flow selftest`, which fails if the schema advertises a
command the real parser does not have.
"""

from __future__ import annotations

from . import __version__

_PROJECT = {'name': 'project', 'flag': '--project', 'type': 'string',
            'required': False, 'default': 'FLOW_PROJECT env',
            'help': 'project uuid, or exact title on the Flow home page'}
_PROFILE = [
    {'name': 'profile', 'flag': '--profile', 'type': 'string', 'required': False,
     'help': 'Chrome profile dir (default ~/chrome-automation)'},
    {'name': 'port', 'flag': '--port', 'type': 'int', 'required': False,
     'default': 9223, 'help': 'CDP port'},
    {'name': 'u', 'flag': '-u/--u', 'type': 'int', 'required': False,
     'default': 0, 'help': 'Google account index in the profile (/u/<n>/)'},
]
_GEN = [
    {'name': 'prompt', 'flag': '-p/--prompt', 'type': 'string', 'required': True,
     'help': 'the generation prompt'},
    {'name': 'project', 'flag': '--project', 'type': 'string', 'required': True,
     'help': 'project uuid or exact title'},
    {'name': 'out', 'flag': '--out', 'type': 'path', 'required': False,
     'help': 'output directory (default runs/<stamp>_<mode>_<slug>)'},
    {'name': 'count', 'flag': '--count', 'type': 'int', 'required': False,
     'default': 1, 'range': '1',
     'note': 'Flow titles every output of one click identically, so x2..x4 '
             'cannot be resolved by name; the CLI refuses them and points at '
             '`flow agent --expect N`', 'help': 'outputs per click (x1 only)'},
    {'name': 'aspect', 'flag': '--aspect', 'type': 'string', 'required': False,
     'values': ['9:16', '16:9', '1:1', '4:3', '3:4'],
     'aliases': ['portrait', 'landscape', 'square', '9x16'],
     'help': 'output aspect ratio'},
    {'name': 'model', 'flag': '--model', 'type': 'string', 'required': False,
     'values': ['Omni 1.1 Flash', 'Omni 1.2 Flash', 'Nano Banana 2.1'],
     'aliases': ['omni', 'omni 1.2', 'nano'],
     'note': 'Veo is disabled by CLI policy and always refused'},
    {'name': 'max_credits', 'flag': '--max-credits', 'type': 'int',
     'required': False, 'default': 15,
     'help': 'refuse if the composer price preview exceeds this'},
    {'name': 'ref', 'flag': '--ref', 'type': 'path (repeatable)',
     'required': False, 'help': 'reference image to upload and attach'},
    {'name': 'download', 'flag': '--download', 'type': 'string',
     'required': False, 'values': ['1080p', '720p', '4K', '2K', 'gif']},
    {'name': 'json', 'flag': '--json', 'type': 'bool', 'required': False,
     'help': 'also print the machine-readable result'},
]

SCHEMA = {
    'name': 'flow',
    'version': __version__,
    'summary': 'Drive Google Flow: map the UI, generate images (free) and '
               'videos (priced), and manage the agent (guidelines, prompts, '
               'multi-image batches).',
    'exit_codes': {
        '0': 'success',
        '1': 'blocked or failed - read stderr/stdout for the reason',
        '2': 'bad usage (argparse)',
    },
    'credits': {
        'image': 0,
        'video_per_4s': 7, 'video_per_6s': 10, 'video_per_8s': 12,
        'video_per_10s': 15,
        'note': 'per output; the composer preview is authoritative and is read '
                'before any click',
    },
    'safety': [
        'video commands refuse when the preview exceeds --max-credits',
        'image commands refuse if the preview is ever non-zero',
        'agent --generate reads the balance before typing and reports the delta',
        'one Chrome profile: do not run alongside another Flow driver',
    ],
    'commands': [
        {'name': 'projects', 'credits': 0,
         'purpose': 'list projects, or create one',
         'args': [_PROFILE, {'name': 'json', 'flag': '--json', 'type': 'bool'},
                  {'name': 'create', 'flag': '--create [NAME]', 'type': 'string',
                   'required': False, 'help': 'create a new project'}],
         'returns': 'uuid + title lines, or {title: uuid} with --json'},
        {'name': 'credits', 'credits': 0, 'purpose': 'read the balance',
         'args': [_PROFILE, {'name': 'json', 'flag': '--json', 'type': 'bool'}],
         'returns': '{credits, credits_num, closed}'},
        {'name': 'probe', 'credits': 0,
         'purpose': 'map the live UI: composer, agent, models, settings, '
                    'instructions, references, menus',
         'args': [_PROFILE, _PROJECT,
                  {'name': 'agent', 'flag': '--agent', 'values':
                   ['leave', 'on', 'off'], 'default': 'leave'},
                  {'name': 'json', 'flag': '--json', 'type': 'bool'}],
         'returns': 'a summary; runs/probes/<stamp>_<label>/probe.json'},
        {'name': 'config', 'credits': 0,
         'purpose': 'read or set the generation config and its credit preview',
         'args': [_PROFILE, _PROJECT,
                  {'name': 'mode', 'values': ['Image', 'Video']},
                  {'name': 'aspect'}, {'name': 'count', 'type': 'int'},
                  {'name': 'model'}, {'name': 'thinking'}, {'name': 'duration'},
                  {'name': 'res'}, {'name': 'submode'}, {'name': 'raw',
                                                       'type': 'bool'}],
         'returns': 'JSON of mode/aspect/model/resolution/duration/outputs/'
                    'thinking/cost_text/cost_credits'},
        {'name': 'image', 'credits': 0,
         'purpose': 'generate image(s) in the direct composer',
         'args': _PROFILE + _GEN,
         'returns': '{files:[{name,file,bytes}], config, cost_preview, '
                    'manifest}'},
        {'name': 'video', 'credits': '7..15 per output',
         'purpose': 'generate video(s), budget-gated by the preview',
         'args': _PROFILE + _GEN + [
             {'name': 'duration', 'flag': '--duration',
              'values': ['4s', '6s', '8s', '10s'],
              'note': 'Omni 1.1 offers 4/6/8/10s; Omni 1.2 (a slider) offers '
                      '3..15s in 1s steps, 15s = 60 credits',
              'aliases': ['10', '15s', '3s']},
             {'name': 'res', 'flag': '--res', 'values': ['360p', '720p'],
              'aliases': ['360', '720', 'hd']},
             {'name': 'submode', 'flag': '--submode',
              'values': ['Ingredients', 'Frames'],
              'note': 'Frames turns the composer into Start/End frame slots; '
                      'each is filled from the project\'s own images by name'},
             {'name': 'start_frame', 'flag': '--start-frame',
              'type': 'string', 'required': False,
              'help': 'project image name for the first frame (frames mode)'},
             {'name': 'end_frame', 'flag': '--end-frame', 'type': 'string',
              'required': False,
              'help': 'project image name for the last frame (frames mode)'},
             {'name': 'dry_run', 'flag': '--dry-run', 'type': 'bool',
              'required': False,
              'help': 'configure, price, frame and type - but do not click'}],
         'returns': 'same shape as image (files end .mp4)'},
        {'name': 'agent', 'credits': '0 for images; post-hoc for video',
         'purpose': 'manage guidelines, type a prompt, or run an image batch',
         'args': _PROFILE + [_PROJECT,
             {'name': 'list', 'flag': '--list', 'type': 'bool',
              'help': 'print stored guidelines as JSON'},
             {'name': 'instructions', 'flag': '--instructions', 'type': 'path',
              'help': 'write guideline blocks (title line + description)'},
             {'name': 'instructions_state', 'flag': '--instructions-state',
              'type': 'bool',
              'help': 'print stored guidelines WITH their active on/off switch'},
             {'name': 'activate_instruction', 'flag': '--activate-instruction',
              'type': 'string', 'repeatable': True, 'metavar': 'TEXT',
              'help': 'turn on the stored guideline whose text contains TEXT'},
             {'name': 'deactivate_instruction', 'flag': '--deactivate-instruction',
              'type': 'string', 'repeatable': True, 'metavar': 'TEXT',
              'help': 'turn off the stored guideline whose text contains TEXT'},
             {'name': 'ref', 'flag': '--ref', 'type': 'path',
              'repeatable': True,
              'help': 'reference image attached to the agent prompt (the style '
                      'lock for image batches; uploaded as a tile first)'},
             {'name': 'prompt', 'flag': '-p/--prompt', 'type': 'string'},
             {'name': 'generate', 'flag': '--generate', 'type': 'bool',
              'help': 'submit the prompt to the agent (can spend)'},
             {'name': 'expect', 'flag': '--expect', 'type': 'int',
              'help': 'harvest this many new tiles the agent creates'},
             {'name': 'out', 'flag': '--out', 'type': 'path'},
             {'name': 'max_credits', 'flag': '--max-credits', 'type': 'int',
              'default': 15},
             {'name': 'no_auto_name', 'flag': '--no-auto-name', 'type': 'bool',
              'help': 'do not append a unique naming directive'},
             {'name': 'skip_credit_read', 'flag': '--skip-credit-read',
              'type': 'bool',
              'note': 'a balance read opens Flow\'s account panel; a stuck '
                      'panel leaves a backdrop that swallows clicks, so '
                      '--expect batches skip it automatically (images are '
                      'free)'}],
         'returns': 'guideline JSON, or {files, manifest} for a batch'},
        {'name': 'schema', 'credits': 0, 'purpose': 'print this contract',
         'args': [{'name': 'json', 'flag': '--json', 'type': 'bool',
                   'default': True}], 'returns': 'this document'},
        {'name': 'selftest', 'credits': 0,
         'purpose': 'offline checks of the pure logic (no Chrome)',
         'args': [], 'returns': 'pass/fail lines; exit 1 on failure'},
    ],
    'examples': [
        'flow projects -u 1',
        'flow probe --project "Flow CLI Test" -u 1 --agent on',
        'flow config --project <uuid> -u 1',
        'flow image -p "a brass key on velvet, 9:16" --project <uuid> -u 1',
        'flow image -p "..." --project <uuid> -u 1 --ref ref.png --count 2',
        'flow video -p "..." --project <uuid> -u 1 --duration 4s --max-credits 8',
        'flow agent --project <uuid> -u 1 --list',
        'flow agent --project <uuid> -u 1 --instructions rules.txt',
        'flow agent --project <uuid> -u 1 -p "Create 6 images ... Image1..6" '
        '--generate --expect 6 --out runs\\batch',
    ],
}
