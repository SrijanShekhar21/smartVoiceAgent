import asyncio
import os
import threading
import queue # For thread-safe queues

from deepgram import (
    DeepgramClient,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone,
)

# --- Helper Class for Transcript Collection ---
class TranscriptCollector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.transcript_parts = []

    def add_part(self, part):
        # Only add non-empty parts to avoid extra spaces
        if part.strip():
            self.transcript_parts.append(part)

    def get_full_transcript(self):
        return ' '.join(self.transcript_parts)

# --- Component 1: Speech-to-Text (STT) using Deepgram ---
class STTListener:
    def __init__(self, stt_to_llm_queue: queue.Queue,
                 user_speaking_event: threading.Event,
                 interrupt_bot_event: threading.Event,
                 bot_speaking_event: threading.Event,
                 exit_event: threading.Event):
        
        self.stt_to_llm_queue = stt_to_llm_queue
        self.user_speaking_event = user_speaking_event
        self.interrupt_bot_event = interrupt_bot_event
        self.bot_speaking_event = bot_speaking_event
        self.exit_event = exit_event
        self.transcript_collector = TranscriptCollector() # Each listener has its own collector

        # Deepgram audio parameters (must match Microphone and LiveOptions)
        self.DG_ENCODING = "linear16"
        self.DG_CHANNELS = 1
        self.DG_SAMPLE_RATE = 16000
        self.DG_ENDPOINTING_MS = 300 # Time in milliseconds Deepgram waits for silence

    # # Deepgram's on_message callback. `dg_connection_instance` is the first arg from SDK.
    # async def on_message(self, dg_connection_instance, result, **kwargs):
    #     sentence = result.channel.alternatives[0].transcript        

    #     # -------- Interruption Logic (User speaking while bot is active) --------
    #     # If user speaks anything, set the user_speaking_event
    #     if len(sentence.strip()) > 0:
    #         if not self.user_speaking_event.is_set():
    #             # This is the first time we detect user speech in this segment
    #             self.user_speaking_event.set() # Signal that user is speaking

    #         # If the bot is currently speaking AND user starts speaking, signal interruption
    #         if self.bot_speaking_event.is_set():
    #             if not self.interrupt_bot_event.is_set(): # Avoid redundant signals
    #                 print("\n[Interrupt] User detected speaking while bot is talking. Signaling bot to stop.")
    #                 self.interrupt_bot_event.set() # Signal TTS thread to stop immediately

    #     # --- Transcript Collection Logic ---
    #     # Deepgram's `speech_final` means this segment is final.
    #     # This typically aligns with SpeechFinal due to endpointing.
    #     if result.speech_final: 
    #         self.transcript_collector.add_part(sentence)
    #         full_sentence = self.transcript_collector.get_full_transcript()
            
    #         # Clear user_speaking_event as this utterance is finalized (implies a pause after this segment)
    #         if self.user_speaking_event.is_set():
    #             self.user_speaking_event.clear()

    #         if full_sentence.strip(): # Only process non-empty sentences
    #             print(f"\nUser: {full_sentence}")
    #             try:
    #                 # Put the full sentence in the queue for the LLM
    #                 # This will block if queue is full, consider put_nowait with error handling
    #                 self.stt_to_llm_queue.put(full_sentence) 
    #             except queue.Full:
    #                 print("[STT] LLM queue is full, skipping sentence.")
            
    #         self.transcript_collector.reset() # Reset for the next utterance
    #     else:
    #         # Interim results, continue collecting
    #         self.transcript_collector.add_part(sentence)
    #         # Only print interim results if bot is not speaking, to avoid clutter/conflicts
    #         if not self.bot_speaking_event.is_set():
    #             # Clear the line and print new interim result
    #             print(f"Interim: {self.transcript_collector.get_full_transcript()}", end='\r')

    async def on_message(self, dg_connection_instance, result, **kwargs):
        sentence = result.channel.alternatives[0].transcript        

        # -------- Interruption Logic (User speaking while bot is active) --------
        # If user speaks anything, set the user_speaking_event
        if len(sentence.strip()) > 0:
            if not self.user_speaking_event.is_set():
                # This is the first time we detect user speech in this segment
                self.user_speaking_event.set() # Signal that user is speaking

            # If the bot is currently speaking AND user starts speaking, signal interruption
            if self.bot_speaking_event.is_set():
                if not self.interrupt_bot_event.is_set(): # Avoid redundant signals
                    print("\n[Interrupt] User detected speaking while bot is talking. Signaling bot to stop.")
                    self.interrupt_bot_event.set() # Signal TTS thread to stop immediately
                    # Important: If the bot is interrupted, clear the interim print line.
                    # This makes the console cleaner when the bot stops talking abruptly.
                    print(" " * os.get_terminal_size().columns, end='\r')


        # --- Transcript Collection Logic ---
        # Deepgram's `speech_final` means this segment is final.
        # This typically aligns with SpeechFinal due to endpointing.
        if result.speech_final: 
            self.transcript_collector.add_part(sentence)
            full_sentence = self.transcript_collector.get_full_transcript()
            
            # Clear user_speaking_event as this utterance is finalized (implies a pause after this segment)
            if self.user_speaking_event.is_set():
                self.user_speaking_event.clear()

            if full_sentence.strip(): # Only process non-empty sentences
                print(f"\nUser: {full_sentence}") # Print final user utterance on a new line
                try:
                    # Put the full sentence in the queue for the LLM
                    # This will block if queue is full, consider put_nowait with error handling
                    # The queue maxsize is 1, so put_nowait is probably better here.
                    self.stt_to_llm_queue.put_nowait(full_sentence) 
                except queue.Full:
                    print("[STT] LLM queue is full, skipping sentence.")
            
            self.transcript_collector.reset() # Reset for the next utterance
        else:
            # Interim results, continue collecting
            self.transcript_collector.add_part(sentence)
            # Only print interim results if bot is not speaking, to avoid clutter/conflicts
            if not self.bot_speaking_event.is_set():
                # Clear the line and print new interim result
                print(f"Interim: {self.transcript_collector.get_full_transcript()}", end='\r')
            # If the bot is speaking, interim results will be ignored on console,
            # but they still contribute to the user_speaking_event and interruption logic.

    async def on_error(self, dg_connection_instance, error, **kwargs):
        print(f"\n\n[Deepgram STT] Error: {error}\n\n")

    async def listen_and_transcribe(self):
        """
        Runs the Deepgram STT listening loop in an asyncio event loop.
        This function is designed to be run in a separate thread.
        """
        try:
            # Deepgram client (API key automatically picked from DEEPGRAM_API_KEY env var)
            deepgram: DeepgramClient = DeepgramClient() # Uses env var

            dg_connection = deepgram.listen.asynclive.v("1")

            dg_connection.on(LiveTranscriptionEvents.Transcript, self.on_message)
            dg_connection.on(LiveTranscriptionEvents.Error, self.on_error)

            options = LiveOptions(
                model="nova-2",
                punctuate=True,
                language="en-IN",
                encoding=self.DG_ENCODING,
                channels=self.DG_CHANNELS,
                sample_rate=self.DG_SAMPLE_RATE,
                interim_results=True, # Get interim results
                # Endpointing is crucial for detecting end of user's turn
                endpointing=self.DG_ENDPOINTING_MS # Time in milliseconds Deepgram waits for silence
            )

            await dg_connection.start(options)
            microphone = Microphone(dg_connection.send)

            print("[STT] Microphone starting...")
            microphone.start()
            print("[STT] Microphone active. Speak now.")

            # Keep microphone active until exit_event is set
            while microphone.is_active() and not self.exit_event.is_set():
                # It's important to await asyncio.sleep to yield control
                # This allows other tasks in this thread's asyncio loop (like on_message) to run
                await asyncio.sleep(0.1) 

            microphone.finish()
            await dg_connection.finish()
            print("[STT] Microphone / Deepgram STT finished.")

        except Exception as e:
            print(f"[STT] Could not open socket or STT error: {e}")

