import asyncio
import os
import sys

import aiohttp
from dotenv import load_dotenv
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.transports.daily.transport import DailyParams, DailyTransport

# Import our custom Deepslate Pipecat plugin
from deepslate.pipecat import DeepslateOptions, DeepslateRealtimeLLMService

load_dotenv(override=True)

logger.remove()
logger.add(sys.stderr, level="DEBUG")

async def main():
    # 1. Initialize Daily Transport
    daily_api_key = os.getenv("DAILY_API_KEY")
    daily_room_url = os.getenv("DAILY_ROOM_URL")

    if not daily_api_key or not daily_room_url:
        logger.error("Please set DAILY_API_KEY and DAILY_ROOM_URL in your .env file")
        return

    # Fetch a meeting token so the bot can join the Daily room
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
            data = await r.json()
            token = data["token"]

    transport = DailyTransport(
        room_url=daily_room_url,
        token=token,
        bot_name="Deepslate Bot",
        params=DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            camera_out_enabled=False,
            vad_enabled=False, # Deepslate handles Voice Activity Detection server-side
        ),
    )

    # 2. Initialize Deepslate LLM Service
    try:
        opts = DeepslateOptions.from_env(
            system_prompt="You are a friendly and helpful AI assistant. Keep your answers concise."
        )
    except ValueError as e:
        logger.error(e)
        return

    # Deepslate Opal takes raw audio in and streams raw audio out.
    llm = DeepslateRealtimeLLMService(options=opts)

    # 3. Build the Pipeline
    pipeline = Pipeline([
        transport.input(),
        llm,
        transport.output(),
    ])

    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        logger.info(f"Participant {participant['id']} joined. Listening...")
        # Deepslate is now listening to the participant's audio.

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