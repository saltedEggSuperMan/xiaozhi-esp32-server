"""Machine-wide audio route shared by voice, local music and the music bridge."""
import copy
from pathlib import Path
import threading
import time
import math
import os
import tempfile
import io


class AudioOutputRouter:
    _lock = threading.Lock()
    _cache = {}

    @classmethod
    def set_volume(cls, config, percent):
        """Persist this app's gain only; no OS/device volume or music stop."""
        if isinstance(percent,bool) or not isinstance(percent,(int,float)) or not math.isfinite(percent) or not 0<=percent<=100:
            raise ValueError('小智音量必须为 0 到 100')
        from ruamel.yaml import YAML
        from core.utils.pcAudioOutput import audio_output_settings
        path=Path(config.get('_audio_output_local_path') or Path(__file__).resolve().parents[2]/'data/.config.yaml')
        with cls._lock:
            yaml=YAML();yaml.preserve_quotes=True
            saved=yaml.load(path.read_text(encoding='utf8')) or {}
            saved.setdefault('audio_output',{}).setdefault('pc',{})['volume']=percent/100
            audio_output_settings(saved)
            buffer=io.StringIO();yaml.dump(saved,buffer)
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',dir=path.parent,prefix='.audio-volume-',delete=False) as file:
                    temporary=Path(file.name);file.write(buffer.getvalue())
                os.replace(temporary,path)
            finally:
                if temporary and temporary.exists():temporary.unlink()
            cls._cache.pop(str(path),None)
            config['audio_output']=copy.deepcopy(saved['audio_output'])
        return percent

    @classmethod
    def settings(cls, config):
        from core.utils.pcAudioOutput import audio_output_settings
        path = config.get('_audio_output_local_path')
        if not path:
            return audio_output_settings(config)
        # A missing/invalid saved file is an error, never a default-device route.
        with cls._lock:
            now = time.monotonic()
            cached = cls._cache.get(path)
            if cached is None or now - cached[0] >= 0.25:
                import yaml
                saved = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
                settings = copy.deepcopy(saved.get('audio_output', {}))
                value = audio_output_settings({'audio_output': settings})
                cls._cache[path] = (now, value)
            return cls._cache[path][1]

    @classmethod
    async def prepare(cls, conn):
        from core.utils.pcAudioOutput import close_pc_audio
        route = cls.settings(conn.config)
        signature = route[:2]
        previous = getattr(conn, '_audio_route_signature', signature)
        if previous != signature:
            await close_pc_audio(conn, release_device=True)
            conn.pc_sound_device = None
            conn.pc_audio_failed_sentence = object()
            controller = getattr(conn, 'audio_rate_controller', None)
            if controller:
                controller.stop_sending()
        conn._audio_route_signature = signature
        return route
