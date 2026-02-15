from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from pathlib import Path
import os

class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        import requests

        proto_dir = Path(self.root) / "deepslate" / "pipecat" / "proto"
        output_path = proto_dir / "realtime.proto"
        no_update_file = proto_dir / ".no-update"

        if no_update_file.exists():
            print("Skipping realtime.proto download because .no-update file exists.")
            return

        if os.environ.get("DEEPSLATE_NO_PROTO_UPDATE"):
            print("Skipping realtime.proto download because DEEPSLATE_NO_PROTO_UPDATE is set.")
            return

        print("Downloading realtime.proto...")

        download = requests.get(
            "https://raw.githubusercontent.com/rooms-solutions/deepslate-docs/refs/heads/main/api-reference/realtime.proto")
        with open(output_path, "w") as f:
            f.write(download.text)

        print("Done.")