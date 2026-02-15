import asyncio
import json
from typing import Any, Optional

import aiohttp
from loguru import logger

from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InterruptionFrame,
    FunctionCallRequestFrame,
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesUpdateFrame,
    LLMTextFrame,
    StartFrame,
    TextFrame,
    TTSTextFrame
)
from pipecat.services.ai_services import LLMService

from .options import DeepslateOptions, DeepslateVadConfig, ElevenLabsTtsConfig
from .proto import realtime_pb2 as proto


class DeepslateRealtimeLLMService(LLMService):
    """
    Pipecat service for Deepslate's end-to-end Speech-to-Speech Realtime API.
    """

    def __init__(
            self,
            options: DeepslateOptions,
            vad_config: Optional[DeepslateVadConfig] = None,
            tts_config: Optional[ElevenLabsTtsConfig] = None,
            **kwargs
    ):
        super().__init__(**kwargs)
        self._opts = options
        self._vad_config = vad_config or DeepslateVadConfig()
        self._tts_config = tts_config

        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None

        self._receive_task: Optional[asyncio.Task] = None
        self._main_task: Optional[asyncio.Task] = None
        self._session_initialized = False
        self._should_stop = False

        # Audio configuration tracking
        self._detected_sample_rate: Optional[int] = None
        self._detected_num_channels: Optional[int] = None
        self._packet_id_counter: int = 0

    async def start(self, frame: StartFrame):
        """Starts the Pipecat service and initializes the WebSocket connection."""
        await super().start(frame)
        self._session = aiohttp.ClientSession()
        self._should_stop = False
        self._main_task = asyncio.create_task(self._main_event_loop())

    async def stop(self, frame: EndFrame):
        """Stops the Pipecat service."""
        self._should_stop = True
        await self._disconnect()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame):
        """Cancels the Pipecat service immediately."""
        self._should_stop = True
        await self._disconnect()
        await super().cancel(frame)

    async def _main_event_loop(self):
        """Main event loop with reconnection support."""
        num_retries = 0
        max_retries = self._opts.max_retries

        while not self._should_stop:
            try:
                await self._connect()
                num_retries = 0  # Reset on successful connection

                # Wait for the receive task to complete (connection closed)
                if self._receive_task:
                    await self._receive_task

                # If we get here, connection was closed
                if not self._should_stop:
                    logger.info("WebSocket connection closed, attempting to reconnect...")

            except aiohttp.ClientError as e:
                if num_retries >= max_retries:
                    error_msg = f"Connection failed after {num_retries} retries: {e}"
                    logger.error(error_msg)
                    await self.push_error(ErrorFrame(error_msg))
                    break

                num_retries += 1
                retry_interval = min(2 ** num_retries, 30)
                logger.warning(
                    f"Connection failed (attempt {num_retries}/{max_retries}), "
                    f"retrying in {retry_interval}s: {e}"
                )
                await asyncio.sleep(retry_interval)

            except Exception as e:
                error_msg = f"Unexpected error in main event loop: {e}"
                logger.error(error_msg)
                await self.push_error(ErrorFrame(error_msg))
                break

        logger.info("Main event loop terminated")

    async def _connect(self):
        """Establish the WebSocket connection to Deepslate."""
        url = self._opts.ws_url
        if not url:
            # Construct standard URL
            base_ws = self._opts.base_url.replace("https://", "wss://").replace("http://", "ws://")
            url = f"{base_ws}/api/v1/vendors/{self._opts.vendor_id}/organizations/{self._opts.organization_id}/realtime"

        headers = {"User-Agent": "PipecatDeepslate/1.0"}
        if self._opts.api_key:
            headers["Authorization"] = f"Bearer {self._opts.api_key}"

        logger.debug(f"Connecting to Deepslate: {url}")

        try:
            self._ws = await self._session.ws_connect(url, headers=headers)
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info("Successfully connected to Deepslate Realtime API.")
        except Exception as e:
            logger.error(f"Failed to connect to Deepslate: {e}")
            await self.push_error(ErrorFrame(f"Connection failed: {str(e)}"))

    async def _disconnect(self):
        """Close tasks and connections cleanly."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass

        if self._ws and not self._ws.closed:
            await self._ws.close()

        if self._session and not self._session.closed:
            await self._session.close()

        self._session_initialized = False

    async def process_frame(self, frame: Frame, direction: Any):
        """Handle incoming frames from the Pipecat pipeline."""

        if isinstance(frame, AudioRawFrame):
            await self._handle_audio_input(frame)

        elif isinstance(frame, TextFrame):
            # Normal text input from the user/pipeline
            await self._handle_text_input(frame.text)

        elif isinstance(frame, FunctionCallResultFrame):
            # When a function finishes in Pipecat, we pass it back to Deepslate
            await self._handle_function_result(frame)

        elif isinstance(frame, LLMMessagesUpdateFrame):
            # Used to update context/system prompts dynamically
            pass # TODO

        else:
            # Pass unhandled frames downstream (e.g. Stop frames)
            await self.push_frame(frame, direction)

    async def _handle_audio_input(self, frame: AudioRawFrame):
        """Forward PCM audio from Pipecat to Deepslate."""
        if not self._ws:
            return

        # Initialize session on first audio frame using Pipecat's frame audio metadata
        if not self._session_initialized:
            self._detected_sample_rate = frame.sample_rate
            self._detected_num_channels = frame.num_channels
            await self._send_initialize_session()
            self._session_initialized = True

        elif (frame.sample_rate != self._detected_sample_rate or
              frame.num_channels != self._detected_num_channels):
            # Audio format changed, reconfigure
            self._detected_sample_rate = frame.sample_rate
            self._detected_num_channels = frame.num_channels

            reconfig = proto.ReconfigureSessionRequest(
                input_audio_line=proto.AudioLineConfiguration(
                    sample_rate=frame.sample_rate,
                    channel_count=frame.num_channels,
                    sample_format=proto.SampleFormat.SIGNED_16_BIT,
                )
            )
            await self._send_msg(proto.ServiceBoundMessage(reconfigure_session_request=reconfig))

        self._packet_id_counter += 1
        user_input = proto.UserInput(
            packet_id=self._packet_id_counter,
            mode=proto.InferenceTriggerMode.IMMEDIATE,
            audio_data=proto.AudioData(data=frame.audio)
        )
        await self._send_msg(proto.ServiceBoundMessage(user_input=user_input))

    async def _handle_text_input(self, text: str):
        """Forward Text frames (if any) as trigger inputs."""
        if not self._ws:
            return

        if not self._session_initialized:
            self._detected_sample_rate = 16000
            self._detected_num_channels = 1
            await self._send_initialize_session()
            self._session_initialized = True

        self._packet_id_counter += 1
        user_input = proto.UserInput(
            packet_id=self._packet_id_counter,
            mode=proto.InferenceTriggerMode.IMMEDIATE,
            text_data=proto.TextData(data=text)
        )
        await self._send_msg(proto.ServiceBoundMessage(user_input=user_input))

    async def _handle_function_result(self, frame: FunctionCallResultFrame):
        """Forward function return values to Deepslate."""
        if not self._ws:
            return

        # Ensure result is sent as a string representation
        result_str = frame.result if isinstance(frame.result, str) else json.dumps(frame.result)
        response = proto.ToolCallResponse(
            id=frame.tool_call_id,
            result=result_str
        )
        await self._send_msg(proto.ServiceBoundMessage(tool_call_response=response))

    async def _send_initialize_session(self):
        """Constructs and sends the initialize payload based on config."""

        def _duration(ms: int):
            return proto.Duration(seconds=ms // 1000, nanos=(ms % 1000) * 1000000)

        tts_config = None
        if self._tts_config:
            el_config = proto.ElevenLabsTtsConfiguration(
                api_key=self._tts_config.api_key,
                voice_id=self._tts_config.voice_id
            )
            if self._tts_config.model_id:
                el_config.model_id = self._tts_config.model_id
            tts_config = proto.TtsConfiguration(eleven_labs=el_config)

        init_request = proto.InitializeSessionRequest(
            input_audio_line=proto.AudioLineConfiguration(
                sample_rate=self._detected_sample_rate,
                channel_count=self._detected_num_channels,
                sample_format=proto.SampleFormat.SIGNED_16_BIT,
            ),
            output_audio_line=proto.AudioLineConfiguration(
                sample_rate=self._detected_sample_rate,
                channel_count=self._detected_num_channels,
                sample_format=proto.SampleFormat.SIGNED_16_BIT,
            ),
            vad_configuration=proto.VadConfiguration(
                confidence_threshold=self._vad_config.confidence_threshold,
                min_volume=self._vad_config.min_volume,
                start_duration=_duration(self._vad_config.start_duration_ms),
                stop_duration=_duration(self._vad_config.stop_duration_ms),
                backbuffer_duration=_duration(self._vad_config.backbuffer_duration_ms),
            ),
            inference_configuration=proto.InferenceConfiguration(
                system_prompt=self._opts.system_prompt,
            ),
            tts_configuration=tts_config,
        )

        msg = proto.ServiceBoundMessage(initialize_session_request=init_request)
        await self._send_msg(msg)

    async def _send_msg(self, msg: proto.ServiceBoundMessage):
        """Helper to serialize and push the Proto down the websocket."""
        try:
            await self._ws.send_bytes(msg.SerializeToString())
        except Exception as e:
            logger.error(f"Error sending message to Deepslate: {e}")

    async def _receive_loop(self):
        """Long running task to receive and handle websocket messages from Deepslate."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    client_msg = proto.ClientBoundMessage()
                    client_msg.ParseFromString(msg.data)
                    await self._handle_server_message(client_msg)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    logger.warning(f"WebSocket closed or errored: {msg.data}")
                    break
        except Exception as e:
            logger.error(f"WebSocket receive error: {e}")

    async def _handle_server_message(self, msg: proto.ClientBoundMessage):
        """Map server protobuf events to Pipecat Frames."""
        payload_type = msg.WhichOneof("payload")

        if payload_type == "response_begin":
            await self.push_frame(LLMFullResponseStartFrame())

        elif payload_type == "response_end":
            await self.push_frame(LLMFullResponseEndFrame())

        elif payload_type == "model_text_fragment":
            text = msg.model_text_fragment.text
            # Push LLM Text for transcript logs/history
            await self.push_frame(LLMTextFrame(text))

            # If we don't have Server side TTS configured, push TTSText
            # so a downstream Pipecat TTS service (like Cartesia/ElevenLabs plugins) can pick it up
            if not self._tts_config:
                await self.push_frame(TTSTextFrame(text))

        elif payload_type == "model_audio_chunk":
            audio_bytes = msg.model_audio_chunk.audio.data
            transcript = msg.model_audio_chunk.transcript

            # Push raw PCM audio down the pipeline (e.g., towards WebRTC transport)
            frame = AudioRawFrame(
                audio=audio_bytes,
                sample_rate=self._detected_sample_rate or 16000,
                num_channels=self._detected_num_channels or 1,
            )
            await self.push_frame(frame)

            # Also push the transcript frame for text UI
            if transcript:
                await self.push_frame(LLMTextFrame(transcript))

        elif payload_type == "playback_clear_buffer":
            # Deepslate native interruption (user started speaking).
            # We emit Pipecat's InterruptionFrame so transports drop currently queued audio
            await self.push_frame(InterruptionFrame())

        elif payload_type == "tool_call_request":
            req = msg.tool_call_request

            # Convert protobuf struct to JSON dict string (Pipecat expects JSON strings for args)
            args_json = "{}"
            if req.HasField("parameters"):
                # Ideally, parse struct to dict here. Using a simplistic approach:
                from google.protobuf.json_format import MessageToDict
                args_dict = MessageToDict(req.parameters)
                # Since Deepslate maps them into a "fields" container sometimes:
                if "fields" in args_dict:
                    args_dict = {k: v.get('stringValue', v) for k, v in args_dict["fields"].items()}
                args_json = json.dumps(args_dict)

            await self.push_frame(
                FunctionCallRequestFrame(
                    tool_call_id=req.id,
                    function_name=req.name,
                    arguments=args_json
                )
            )