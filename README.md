# deepslate-pipecat

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Documentation](https://img.shields.io/badge/docs-deepslate.eu-green)](https://docs.deepslate.eu/)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Pipecat plugin for [Deepslate's](https://deepslate.eu/) realtime speech-to-speech AI API.

## Overview

`deepslate-pipecat` provides an `LLMService` implementation for the [Pipecat](https://github.com/pipecat-ai/pipecat) framework, enabling seamless integration with Deepslate's unified voice AI infrastructure. This plugin connects your Pipecat pipelines to Deepslate's real-time speech-to-speech API, providing low-latency voice AI capabilities with server-side processing.

With `deepslate-pipecat`, you can build voice agents that leverage Deepslate's infrastructure for voice activity detection, speech recognition, LLM inference, and text-to-speech - all through a simple, Pipecat-native interface. The plugin handles bidirectional audio streaming, frame translation, and WebSocket connection management automatically.

### Key Features

- **Real-time Audio Streaming** - Low-latency bidirectional PCM audio streaming over WebSockets
- **Server-side VAD** - Voice Activity Detection handled server-side by Deepslate with configurable sensitivity
- **Function Calling** - Full support for tool/function calling with automatic request/response handling
- **Flexible TTS** - Choose between server-side ElevenLabs TTS (via Deepslate) or client-side Pipecat TTS services
- **Automatic Interruption Handling** - Native support for interruptions with buffer clearing
- **Frame-based Architecture** - Seamless integration with Pipecat's frame pipeline model
- **Dynamic Audio Configuration** - Automatically adapts to audio format changes during runtime

## Installation

```bash
pip install git+https://github.com/deepslate-labs/deepslate-pipecat.git
```

### Requirements

- Python 3.11 or higher
- Active internet connection for WebSocket communication

### Dependencies

The following dependencies are automatically installed:

- `pipecat-ai>=0.0.40` - Core Pipecat framework
- `aiohttp>=3.10.0` - Async HTTP/WebSocket client
- `protobuf>=5.26.0` - Protocol buffer serialization
- `loguru>=0.7.2` - Structured logging
- `websockets>=16.0` - WebSocket client/server implementation

## Prerequisites

### Deepslate Account Setup

You'll need a Deepslate account with access to the realtime API. Sign up at [deepslate.eu](https://deepslate.eu) and obtain your credentials.

### Required Environment Variables

Set the following environment variables in your `.env` file or system environment:

```bash
# Required: Deepslate API credentials
DEEPSLATE_VENDOR_ID=your_vendor_id
DEEPSLATE_ORGANIZATION_ID=your_organization_id
DEEPSLATE_API_KEY=your_api_key
```

### Optional Environment Variables

For server-side text-to-speech with ElevenLabs (recommended for best interruption handling):

```bash
# Optional: ElevenLabs TTS configuration
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID=your_voice_id  # e.g., '21m00Tcm4TlvDq8ikWAM' for Rachel
ELEVENLABS_MODEL_ID=eleven_turbo_v2  # optional, uses default if not set
```

> **Note:** If you don't provide ElevenLabs TTS configuration, the service will emit `TTSTextFrame` objects that can be picked up by downstream Pipecat TTS services (like Cartesia, Azure TTS, etc.). However, context truncation during interruptions will not work without server-side TTS.

## Quick Start

Here's a complete example that creates a voice bot using Deepslate and Daily.co transport, including ElevenLabs TTS and tool calling:

```python
import asyncio
import os
import random
import sys

import aiohttp
from dotenv import load_dotenv
from loguru import logger

from pipecat.frames.frames import LLMSetToolsFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.daily.transport import DailyParams, DailyTransport

from deepslate.pipecat import DeepslateOptions, DeepslateRealtimeLLMService, ElevenLabsTtsConfig

load_dotenv(override=True)

logger.remove()
logger.add(sys.stderr, level="DEBUG")

# Tool definitions (OpenAI function-calling JSON schema format)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_weather",
            "description": "Get the current weather for a given location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "The city to look up."}
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_location",
            "description": "Get the user's current location.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

async def lookup_weather(params: FunctionCallParams):
    result = {
        "location": params.arguments.get("location", "unknown"),
        "temperature_celsius": random.randint(10, 35),
        "precipitation": random.choice(["none", "light", "moderate", "heavy"]),
        "air_pressure_hpa": random.randint(900, 1100),
    }
    await params.result_callback(result)

async def get_current_location(params: FunctionCallParams):
    await params.result_callback({"location": "Berlin"})

async def main():
    # 1. Initialize Daily Transport
    daily_api_key = os.getenv("DAILY_API_KEY")
    daily_room_url = os.getenv("DAILY_ROOM_URL")

    if not daily_api_key or not daily_room_url:
        logger.error("Please set DAILY_API_KEY and DAILY_ROOM_URL in your .env file")
        return

    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {daily_api_key}"}
        room_name = daily_room_url.split("/")[-1]
        async with session.post(
            "https://api.daily.co/v1/meeting-tokens",
            headers=headers,
            json={"properties": {"room_name": room_name}}
        ) as r:
            if r.status != 200:
                logger.error(f"Failed to get Daily token: {await r.text()}")
                return
            token = (await r.json())["token"]

    transport = DailyTransport(
        room_url=daily_room_url,
        token=token,
        bot_name="Deepslate Bot",
        params=DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            camera_out_enabled=False,
            vad_enabled=False,  # Deepslate handles VAD server-side
        ),
    )

    # 2. Initialize Deepslate LLM Service
    opts = DeepslateOptions.from_env(
        system_prompt="You are a friendly and helpful AI assistant. Keep your answers concise."
    )
    tts = ElevenLabsTtsConfig.from_env()
    llm = DeepslateRealtimeLLMService(options=opts, tts_config=tts)

    # Register function handlers
    llm.register_function("lookup_weather", lookup_weather)
    llm.register_function("get_current_location", get_current_location)

    # 3. Build the Pipeline
    pipeline = Pipeline([transport.input(), llm, transport.output()])
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    # Sync tool definitions with Deepslate (queued after StartFrame)
    await task.queue_frame(LLMSetToolsFrame(tools=TOOLS))

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        logger.info(f"Participant {participant['id']} joined. Listening...")

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        logger.info(f"Participant {participant['id']} left.")
        await task.cancel()

    # 4. Run the Pipeline
    runner = PipelineRunner()
    logger.info("Starting pipeline runner...")
    await runner.run(task)

if __name__ == "__main__":
    asyncio.run(main())
```

## Configuration Reference

### DeepslateOptions

The main configuration class for connecting to Deepslate's API.

#### Parameters

| Parameter         | Type            | Default                      | Description                                                    |
|-------------------|-----------------|------------------------------|----------------------------------------------------------------|
| `vendor_id`       | `str`           | **Required**                 | Your Deepslate vendor ID                                       |
| `organization_id` | `str`           | **Required**                 | Your Deepslate organization ID                                 |
| `api_key`         | `str`           | **Required**                 | Your Deepslate API key                                         |
| `base_url`        | `str`           | `"https://app.deepslate.eu"` | Base URL for Deepslate API                                     |
| `system_prompt`   | `str`           | `"You are a helpful assistant."` | System prompt that defines the AI assistant's behavior     |
| `ws_url`          | `Optional[str]` | `None`                       | Optional direct WebSocket URL (for local development/testing)  |
| `max_retries`     | `int`           | `3`                          | Maximum reconnection attempts before giving up                 |

#### Factory Method: `from_env()`

Create configuration from environment variables:

```python
from deepslate.pipecat import DeepslateOptions

# Load from environment variables
opts = DeepslateOptions.from_env()

# Override specific values
opts = DeepslateOptions.from_env(
    system_prompt="You are a customer service agent. Be professional and helpful.",
    max_retries=5  # Increase retry attempts for unstable networks
)

# Manual configuration (not recommended for production)
opts = DeepslateOptions(
    vendor_id="your_vendor_id",
    organization_id="your_org_id",
    api_key="your_api_key",
    system_prompt="Custom prompt here",
    max_retries=3
)
```

### DeepslateVadConfig

Configure server-side Voice Activity Detection parameters.

#### Parameters

| Parameter                  | Type    | Default | Range      | Description                                                       |
|----------------------------|---------|---------|------------|-------------------------------------------------------------------|
| `confidence_threshold`     | `float` | `0.5`   | `0.0-1.0`  | Minimum confidence required to consider audio as speech           |
| `min_volume`               | `float` | `0.01`  | `0.0-1.0`  | Minimum volume level to consider audio as speech                  |
| `start_duration_ms`        | `int`   | `200`   | `>0`       | Duration of speech (ms) required to detect start of speech        |
| `stop_duration_ms`         | `int`   | `500`   | `>0`       | Duration of silence (ms) required to detect end of speech         |
| `backbuffer_duration_ms`   | `int`   | `1000`  | `>0`       | Duration of audio (ms) to buffer before speech detection triggers |

#### Usage Example

```python
from deepslate.pipecat import DeepslateVadConfig, DeepslateRealtimeLLMService

# Use defaults
llm = DeepslateRealtimeLLMService(options=opts)

# Custom VAD configuration (more sensitive)
vad_config = DeepslateVadConfig(
    confidence_threshold=0.3,  # Lower threshold = more sensitive
    min_volume=0.005,          # Detect quieter speech
    start_duration_ms=100,     # Faster detection
    stop_duration_ms=300,      # Shorter pause to end speech
    backbuffer_duration_ms=500 # Less audio backbuffer
)

llm = DeepslateRealtimeLLMService(
    options=opts,
    vad_config=vad_config
)
```

#### VAD Tuning Guidelines

- **High sensitivity** (noisy environments): Increase `confidence_threshold` (0.6-0.8) and `min_volume` (0.02-0.05)
- **Low latency** (fast response): Decrease `start_duration_ms` (100-150) and `stop_duration_ms` (200-300)
- **Natural conversations**: Keep defaults or slightly increase `stop_duration_ms` (600-800)
- **Capture sentence starts**: Increase `backbuffer_duration_ms` (1500-2000)

### ElevenLabsTtsConfig

Configure server-side text-to-speech using ElevenLabs (via Deepslate).

#### Parameters

| Parameter  | Type            | Default | Description                                                        |
|------------|-----------------|---------|--------------------------------------------------------------------|
| `api_key`  | `str`           | **Required** | Your ElevenLabs API key                                       |
| `voice_id` | `str`           | **Required** | ElevenLabs voice ID (e.g., `'21m00Tcm4TlvDq8ikWAM'` for Rachel) |
| `model_id` | `Optional[str]` | `None`  | ElevenLabs model ID (e.g., `'eleven_turbo_v2'`), uses default if not set |

#### Factory Method: `from_env()`

```python
from deepslate.pipecat import ElevenLabsTtsConfig, DeepslateRealtimeLLMService

# Load from environment variables
tts_config = ElevenLabsTtsConfig.from_env()

llm = DeepslateRealtimeLLMService(
    options=opts,
    tts_config=tts_config
)

# Manual configuration
tts_config = ElevenLabsTtsConfig(
    api_key="your_elevenlabs_key",
    voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel voice
    model_id="eleven_turbo_v2"
)
```

#### Server-side vs Client-side TTS

**With server-side TTS (recommended):**
```python
# Audio comes directly from Deepslate with ElevenLabs TTS
tts_config = ElevenLabsTtsConfig.from_env()
llm = DeepslateRealtimeLLMService(options=opts, tts_config=tts_config)
```

**With client-side TTS:**
```python
# Text is emitted as TTSTextFrame for downstream Pipecat TTS services
from pipecat.services.cartesia import CartesiaTTSService

llm = DeepslateRealtimeLLMService(options=opts)  # No tts_config
tts = CartesiaTTSService(...)

pipeline = Pipeline([
    transport.input(),
    llm,
    tts,  # Client-side TTS
    transport.output(),
])
```

> **Important:** Server-side TTS provides better interruption handling because Deepslate can truncate the response context when users interrupt. With client-side TTS, the LLM doesn't know what was actually spoken.

## Integration Guide

### Frame Flow

The Deepslate service processes and emits the following Pipecat frames:

**Input Frames (Consumed):**
- `AudioRawFrame` - PCM audio from user (sent to Deepslate for STT + inference)
- `TextFrame` - Text input from user (sent to Deepslate for inference)
- `FunctionCallResultFrame` - Results from executed functions (sent back to Deepslate)
- `LLMMessagesUpdateFrame` - Context/message history updates
- `StartFrame`, `EndFrame`, `CancelFrame` - Lifecycle management

**Output Frames (Emitted):**
- `LLMFullResponseStartFrame` - Marks the beginning of AI response
- `LLMFullResponseEndFrame` - Marks the end of AI response
- `LLMTextFrame` - Text transcript of AI response
- `TTSTextFrame` - Text for TTS (only if no server-side TTS configured)
- `OutputAudioRawFrame` - PCM audio output (if server-side TTS configured)
- `InterruptionFrame` - User interrupted AI response (buffer clearing signal)
- `FunctionCallRequestFrame` - Request to execute a function/tool
- `ErrorFrame` - Error occurred during processing

### Transport Integration Examples

#### Daily.co (WebRTC)

```python
from pipecat.transports.services.daily import DailyTransport, DailyParams

transport = DailyTransport(
    room_url=daily_room_url,
    token=token,
    bot_name="My Voice Bot",
    params=DailyParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_enabled=False,  # Deepslate handles VAD
    ),
)

pipeline = Pipeline([
    transport.input(),
    llm,
    transport.output(),
])
```

#### Twilio

```python
from pipecat.transports.services.twilio import TwilioTransport

transport = TwilioTransport(
    account_sid=twilio_account_sid,
    auth_token=twilio_auth_token,
    from_number=twilio_from_number,
)

pipeline = Pipeline([
    transport.input(),
    llm,
    transport.output(),
])
```

#### Generic WebSocket

```python
from pipecat.transports.network.websocket import WebsocketTransport

transport = WebsocketTransport(
    host="0.0.0.0",
    port=8765,
    params=WebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
)

pipeline = Pipeline([
    transport.input(),
    llm,
    transport.output(),
])
```

### Function Calling

Deepslate supports function/tool calling via Pipecat's `register_function` API. Define your tools as OpenAI-style JSON schemas, register async handlers, and push the definitions through the pipeline before it starts:

```python
import random
from pipecat.frames.frames import LLMSetToolsFrame
from pipecat.services.llm_service import FunctionCallParams

# 1. Define tools in OpenAI function-calling JSON schema format
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_weather",
            "description": "Get the current weather for a given location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "The city to look up."}
                },
                "required": ["location"],
            },
        },
    },
]

# 2. Implement async handlers — call result_callback with your return value
async def lookup_weather(params: FunctionCallParams):
    result = {
        "location": params.arguments.get("location", "unknown"),
        "temperature_celsius": random.randint(10, 35),
        "precipitation": random.choice(["none", "light", "moderate", "heavy"]),
        "air_pressure_hpa": random.randint(900, 1100),
    }
    await params.result_callback(result)

# 3. Register handlers on the LLM service
llm.register_function("lookup_weather", lookup_weather)

# 4. Queue tool definitions — they are synced to Deepslate after the pipeline starts
await task.queue_frame(LLMSetToolsFrame(tools=TOOLS))
```

See [`examples/simple_bot.py`](examples/simple_bot.py) for a complete working example with multiple tools.

### Error Handling Best Practices

```python
import asyncio
from loguru import logger
from pipecat.frames.frames import ErrorFrame

# 1. Handle connection errors during startup
try:
    opts = DeepslateOptions.from_env()
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    return

# 2. Monitor error frames in the pipeline
async def error_handler(frame):
    if isinstance(frame, ErrorFrame):
        logger.error(f"Pipeline error: {frame.error}")
        # Optionally notify users or restart

# 3. Handle WebSocket disconnections gracefully
@transport.event_handler("on_disconnected")
async def on_disconnected(transport):
    logger.warning("Transport disconnected, attempting reconnection...")
    await asyncio.sleep(2)
    # Implement reconnection logic

# 4. Set timeouts for long-running operations
try:
    await asyncio.wait_for(runner.run(task), timeout=3600)
except asyncio.TimeoutError:
    logger.error("Pipeline timeout after 1 hour")
    await task.cancel()
```

## Troubleshooting

### Common Issues and Solutions

#### Connection Failures

**Problem:** `Failed to connect to Deepslate: Connection refused`

**Solutions:**
- Verify your credentials are correct in `.env` file
- Check internet connectivity
- Ensure `DEEPSLATE_VENDOR_ID`, `DEEPSLATE_ORGANIZATION_ID`, and `DEEPSLATE_API_KEY` are set
- Try accessing `https://app.deepslate.eu` in browser to verify service availability
- Check firewall settings for WebSocket connections (wss://)

**Automatic Reconnection:**
The plugin automatically retries failed connections using exponential backoff:
- 1st retry: after 2 seconds
- 2nd retry: after 4 seconds
- 3rd retry: after 8 seconds
- Further retries: capped at 30 seconds

After `max_retries` (default: 3) failed attempts, an `ErrorFrame` is emitted and the connection stops. You can configure the retry limit:

```python
opts = DeepslateOptions.from_env(max_retries=5)  # Retry up to 5 times
```

#### Missing Environment Variables

**Problem:** `ValueError: Deepslate vendor ID required`

**Solution:**
```bash
# Create a .env file in your project root:
cat > .env << EOF
DEEPSLATE_VENDOR_ID=your_vendor_id
DEEPSLATE_ORGANIZATION_ID=your_org_id
DEEPSLATE_API_KEY=your_api_key
EOF

# Load in Python:
from dotenv import load_dotenv
load_dotenv()
```

#### Audio Format Mismatches

**Problem:** No audio output or garbled audio

**Solutions:**
- Deepslate expects PCM signed 16-bit audio
- Verify sample rate matches between transport and Deepslate (common: 16000, 24000, 48000 Hz)
- Check number of channels (mono=1, stereo=2)
- Enable debug logging to see detected audio configuration:
  ```python
  from loguru import logger
  import sys
  logger.remove()
  logger.add(sys.stderr, level="DEBUG")
  ```

#### No Response from LLM

**Problem:** Audio is sent but no response is received

**Solutions:**
- Check if VAD settings are too strict (increase sensitivity)
- Verify system prompt is valid
- Enable debug logging to see server messages
- Check for error frames in the pipeline
- Ensure sufficient audio is being sent (VAD needs minimum duration)

#### Import Errors

**Problem:** `ModuleNotFoundError: No module named 'pipecat'`

**Solution:**
```bash
pip install --upgrade deepslate-pipecat
# or install from source:
pip install -e .
```

#### Protobuf Version Conflicts

**Problem:** `TypeError: Descriptors cannot not be created directly`

**Solution:**
```bash
# Ensure protobuf version is compatible
pip install --upgrade "protobuf>=5.26.0"

# If still failing, try:
pip uninstall protobuf
pip install "protobuf==5.26.1"
```

#### WebSocket Timeout

**Problem:** Connection drops after period of inactivity

**Solution:**
- Implement heartbeat/keepalive mechanism
- Check network stability
- Verify no proxy/firewall is timing out long-lived connections

### Debug Logging

Enable detailed logging to troubleshoot issues:

```python
from loguru import logger
import sys

# Remove default handler and add custom one
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level="DEBUG"
)

# Now run your pipeline - you'll see detailed Deepslate WebSocket messages
```

## Examples

### Daily.co Voice Bot (WebRTC)

[`examples/simple_bot.py`](examples/simple_bot.py) — A complete voice bot using Daily.co WebRTC transport with ElevenLabs TTS and two example tool calls (`lookup_weather`, `get_current_location`).

```bash
# Set credentials in examples/.env, then:
python examples/simple_bot.py
```

This example includes ElevenLabs TTS and tool call implementations and serves as the recommended starting point for new integrations.

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Pipecat Pipeline                        │
│                                                              │
│  ┌──────────────┐   ┌────────────────────┐   ┌───────────┐ │
│  │   Transport  │──▶│  Deepslate Service │──▶│ Transport │ │
│  │   Input      │   │  (LLMService)      │   │  Output   │ │
│  │ (AudioRaw)   │   │                    │   │(AudioRaw) │ │
│  └──────────────┘   └────────────────────┘   └───────────┘ │
│                              │                               │
└──────────────────────────────┼───────────────────────────────┘
                               │
                               │ WebSocket (Protobuf)
                               │
                    ┌──────────▼──────────┐
                    │  Deepslate Backend  │
                    │                     │
                    │  ┌───────────────┐  │
                    │  │   STT (VAD)   │  │
                    │  ├───────────────┤  │
                    │  │      LLM      │  │
                    │  ├───────────────┤  │
                    │  │  TTS (11Labs) │  │
                    │  └───────────────┘  │
                    └─────────────────────┘
```

### Data Flow

1. **Audio Input**: Transport captures user audio → `AudioRawFrame` → Deepslate Service
2. **WebSocket Upload**: Service sends protobuf `UserInput` with PCM data to Deepslate
3. **Server Processing**: Deepslate performs VAD, STT, LLM inference, and TTS
4. **Response Streaming**: Deepslate streams back text fragments and/or audio chunks
5. **Frame Emission**: Service converts protobuf messages to Pipecat frames
6. **Audio Output**: Frames flow through pipeline to transport → user hears response

## Performance Considerations

### Latency Optimization

- **Server-side TTS**: Lower latency than client-side TTS (fewer hops)
- **VAD Tuning**: Aggressive VAD settings reduce latency but may clip speech
- **Audio Format**: 16kHz mono provides best balance of quality and bandwidth
- **Network**: Use wired connections or stable WiFi for consistent latency

### Resource Usage

- **Memory**: Minimal (WebSocket buffers + frame queues)
- **CPU**: Low (protobuf serialization only, no audio processing)
- **Network**: ~128 kbps for 16kHz mono audio (bidirectional)

## Development

### Building from Source

```bash
git clone https://github.com/rooms-solutions/deepslate-pipecat.git
cd deepslate-pipecat
pip install -e .
```

### Regenerating Protobuf Files

If you modify `deepslate/pipecat/proto/realtime.proto`:

```bash
pip install hatch
hatch build
```

### Running Tests

```bash
# Install test dependencies
pip install -e ".[test]"

# Run tests
pytest tests/
```

## Documentation

- [Deepslate Documentation](https://docs.deepslate.eu/)
- [Pipecat Documentation](https://docs.pipecat.ai/)
- [API Reference](https://docs.deepslate.eu/api-reference/)

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

### Contribution Areas

- Bug fixes and improvements
- Additional examples and tutorials
- Documentation enhancements
- Test coverage expansion
- Performance optimizations

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/rooms-solutions/deepslate-pipecat/issues)
- **Documentation**: [docs.deepslate.eu](https://docs.deepslate.eu/)
- **Email**: support@deepslate.eu
