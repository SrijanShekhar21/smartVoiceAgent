import asyncio
from dotenv import load_dotenv
import os
import threading
import queue # For thread-safe queues

# Import your custom components
from stt_component import STTListener
from llm_component import LLMProcessor
from tts_component import TTSPlayer

def main_orchestrator():
    print("--- Initializing Voice Agent ---")

    # Load environment variables (API keys)
    load_dotenv()

    # --- Queues for inter-thread communication (thread-safe) ---
    # Maxsize 1 prevents backlogs, ensuring processing of the latest input/response.
    stt_to_llm_queue = queue.Queue(maxsize=1) 
    llm_to_tts_queue = queue.Queue(maxsize=1)

    # --- Events for synchronization and interruption (thread-safe) ---
    user_speaking_event = threading.Event()  # Set when user is detected speaking
    bot_speaking_event = threading.Event()   # Set when bot is actively playing audio
    interrupt_bot_event = threading.Event()  # Set by STT if user interrupts bot
    exit_event = threading.Event()           # Set by main thread to signal all threads to exit

    # --- Create instances of the component classes, passing necessary queues and events ---
    stt_listener = STTListener(
        stt_to_llm_queue=stt_to_llm_queue,
        user_speaking_event=user_speaking_event,
        interrupt_bot_event=interrupt_bot_event,
        bot_speaking_event=bot_speaking_event,
        exit_event=exit_event
    )
    llm_processor = LLMProcessor(
        stt_to_llm_queue=stt_to_llm_queue,
        llm_to_tts_queue=llm_to_tts_queue,
        exit_event=exit_event
    )
    tts_player = TTSPlayer(
        llm_to_tts_queue=llm_to_tts_queue,
        interrupt_bot_event=interrupt_bot_event,
        bot_speaking_event=bot_speaking_event,
        exit_event=exit_event
    )

    # --- Create and start threads for each component ---
    # STT and LLM threads need their own asyncio event loops because their main functions
    # (`listen_and_transcribe`, `process_llm_requests`) use async/await.
    # The lambda function creates a new event loop and runs the async function within it.
    stt_thread = threading.Thread(target=lambda: asyncio.run(stt_listener.listen_and_transcribe()), 
                                  name="STT_Thread")
    llm_thread = threading.Thread(target=lambda: asyncio.run(llm_processor.process_llm_requests()),
                                  name="LLM_Thread")
    # TTS thread is designed to be blocking (using time.sleep for now), so it runs directly.
    tts_thread = threading.Thread(target=tts_player.play_tts, 
                                  name="TTS_Thread")

    stt_thread.start()
    llm_thread.start()
    tts_thread.start()

    print("\n--- Voice Agent Started ---")
    print("Speak into your microphone. Press Enter to gracefully stop the agent.")

    # Main thread waits for user input to signal shutdown
    try:
        input() # This will block until Enter is pressed
    except KeyboardInterrupt:
        print("\nCtrl+C detected.")
    finally:
        print("Initiating graceful shutdown...")
        # Set the global exit event to signal all threads to stop
        exit_event.set()

        # Wait for all threads to complete their cleanup and exit
        # This prevents the main program from terminating before threads finish
        stt_thread.join()
        llm_thread.join()
        tts_thread.join()

        print("--- Voice Agent Stopped ---")

if __name__ == "__main__":
    main_orchestrator()