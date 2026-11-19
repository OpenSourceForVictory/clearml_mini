"""Build the UI-downloadable, deterministic SDK bundle from maintained sources."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'frontend/public/integration/mini-eval-sdk.zip'
MODULES = ('sdk command_sdk task_input cs_contract episode_contract parameter_contract '
           'template_contract image_runtime mount_contract resource_contract readiness_contract '
           'cs_entry service_entry supervise process_command host_episode '
           'process_log_contract process_log_capture process_log_store process_log_run').split()

def build(output=OUTPUT):
    files = {f'runner/{name}.py': ROOT/f'runner/{name}.py' for name in MODULES}
    files.update({p.name: p for p in (ROOT/'examples/integration').iterdir() if p.is_file()})
    files.update({f'runtime_templates/{name}': ROOT/f'examples/runtime_templates/{name}'
                  for name in ('client.py', 'episode.py', 'server.py')})
    files['catalog.json'] = ROOT/'frontend/public/template-examples/catalog.json'
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name, source in sorted(files.items()):
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    return output

if __name__ == '__main__':
    print(build())
