import queue
import threading
import time
import os
import io
import simpleaudio as sa # Import simpleaudio directly for granular control
from google.cloud import texttospeech
from pydub import AudioSegment

class TTSPlayer:
    def __init__(self, llm_to_tts_queue: queue.Queue,
                 interrupt_bot_event: threading.Event,
                 bot_speaking_event: threading.Event,
                 exit_event: threading.Event):
        
        self.llm_to_tts_queue = llm_to_tts_queue
        self.interrupt_bot_event = interrupt_bot_event
        self.bot_speaking_event = bot_speaking_event
        self.exit_event = exit_event

        # Configure Google TTS client
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "../google_tts_credentials.json"
        self.client = texttospeech.TextToSpeechClient()
        self.voice = texttospeech.VoiceSelectionParams(
            language_code="en-US",
            name='en-US-Studio-O'  # You can change this to any available voice
        )

        self.audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            pitch=0.0,
            speaking_rate=1.0
            # If you needed word-level synchronization for printing words
            # as they are spoken, you would add:
            # enable_time_pointing=[texttospeech.SsmlVoiceGender.WORD]
            # And then parse response.word_info
        )

        self._current_play_object: sa.PlayObject = None # To hold the simpleaudio PlayObject for stopping
        self._playback_thread: threading.Thread = None # To hold the thread playing the audio
        self._playback_finished_event = threading.Event() # Signals when the audio playback thread completes

    def synthesize_speech(self, text: str) -> bytes:
        """
        Synthesize speech from text using Google TTS and return audio content as bytes.
        """
        synthesis_input = texttospeech.SynthesisInput(text=text)
        response = self.client.synthesize_speech(
            input=synthesis_input,
            voice=self.voice,
            audio_config=self.audio_config
        )
        # If enable_time_pointing was used, response.word_info would be here
        return response.audio_content

    def _play_audio_simpleaudio(self, audio_segment: AudioSegment):
        """
        Internal function to play an AudioSegment using simpleaudio.
        This function is designed to be run in a separate thread.
        It handles actual audio playback and checks for interruption/exit events.
        """
        self.bot_speaking_event.set() # Signal that bot is now speaking (set at start of actual playback)
        self._playback_finished_event.clear() # Reset the event for this new playback
        self._current_play_object = None # Clear any previous play object

        try:
            # Convert pydub.AudioSegment to simpleaudio playable buffer
            wave_object = sa.play_buffer(
                audio_segment.raw_data,
                num_channels=audio_segment.channels,
                bytes_per_sample=audio_segment.sample_width,
                sample_rate=audio_segment.frame_rate
            )
            self._current_play_object = wave_object # Store it to allow stopping

            # Loop to keep the thread alive while audio is playing
            # and allow for interruption/exit checks.
            while wave_object.is_playing():
                # Check for global exit signal or user interruption
                if self.exit_event.is_set():
                    print("[TTS Internal Playback] Global exit detected. Stopping simpleaudio playback.")
                    wave_object.stop() # Immediately stop the simpleaudio playback
                    break # Exit the playback loop
                if self.interrupt_bot_event.is_set():
                    print("[TTS Internal Playback] User interruption detected. Stopping simpleaudio playback.")
                    wave_object.stop() # Immediately stop the simpleaudio playback
                    self.interrupt_bot_event.clear() # Clear the interruption signal for next use
                    break # Exit the playback loop
                
                time.sleep(0.01) # Small sleep to prevent busy-waiting

        except Exception as e:
            print(f"[TTS Internal Playback] Error during simpleaudio playback: {e}")
            print("Ensure simpleaudio is installed and your audio device is available.")
        finally:
            # Always clear the bot_speaking_event when playback finishes or is interrupted
            self.bot_speaking_event.clear()
            # Signal that this specific playback task has finished/stopped
            self._playback_finished_event.set()
            self._current_play_object = None # Clean up reference

    def play_tts(self):
        """
        Continuously pulls text from queue, synthesizes it, and plays it.
        Includes non-pausing playback with real-time interruption logic.
        This function is designed to be run in a separate thread.
        """
        print("[TTS] Player ready.")
        while not self.exit_event.is_set():
            try:
                # Blocking get with a timeout to allow checking exit_event periodically
                text_to_speak = self.llm_to_tts_queue.get(timeout=0.1) 
                
                if text_to_speak:
                    print(f"\n[TTS] Bot speaking: '{text_to_speak}'")
                    
                    # Synthesize Speech (this is blocking, but usually fast for typical sentences)
                    audio_content = self.synthesize_speech(text_to_speak)
                    
                    # Load into pydub.AudioSegment for easy handling
                    audio = AudioSegment.from_file(io.BytesIO(audio_content), format="mp3")

                    # Start the actual audio playback in a dedicated background thread
                    self._playback_thread = threading.Thread(
                        target=self._play_audio_simpleaudio,
                        args=(audio,)
                    )
                    self._playback_thread.daemon = True # Allow main program to exit if this thread is still running
                    self._playback_thread.start()

                    # --- Main thread logic: Monitor playback progress and interruption ---
                    # This loop runs concurrently with the actual audio playback
                    total_duration_ms = len(audio)
                    start_playback_time = time.time()
                    last_printed_progress_ms = -1000 # Initialize to ensure first print happens
                    progress_print_interval_ms = 1000 # Print progress every 1 second

                    print(f"  [TTS Monitoring Playback for '{text_to_speak[:40]}...']")
                    while not self._playback_finished_event.is_set():
                        # Calculate elapsed time from the start of this audio segment's playback
                        current_elapsed_ms = (time.time() - start_playback_time) * 1000

                        # Optional: Print progress to visualize concurrent operation
                        if current_elapsed_ms - last_printed_progress_ms >= progress_print_interval_ms:
                            # Use \r to overwrite the line for a dynamic progress indicator
                            print(f"  [TTS Progress: {current_elapsed_ms/1000:.1f}s / {total_duration_ms/1000:.1f}s]", end='\r', flush=True)
                            last_printed_progress_ms = current_elapsed_ms

                        # Check for global exit or user interruption from the main thread's perspective.
                        # The _play_audio_simpleaudio thread also checks these and handles the actual stopping.
                        # This allows the main thread to react and potentially print messages earlier.
                        if self.exit_event.is_set():
                            print("\n[TTS Main Loop] Global exit detected. Signaling playback thread to stop.")
                            # The audio thread will handle its own stopping and event clearing
                            break
                        if self.interrupt_bot_event.is_set():
                            print("\n[TTS Main Loop] User interruption detected. Signaling playback thread to stop.")
                            # The audio thread will handle its own stopping and event clearing
                            break

                        time.sleep(0.01) # Small sleep to prevent busy-waiting

                    # Ensure the playback thread has fully completed/stopped before processing the next TTS item
                    if self._playback_thread and self._playback_thread.is_alive():
                        # Give it a short timeout to finish naturally or react to the stop signal
                        self._playback_thread.join(timeout=1)
                        if self._playback_thread.is_alive():
                            print("[TTS] Playback thread did not terminate cleanly within timeout.")
                    
                    # Clear the dynamic progress line for neatness after playback is done
                    print(" " * 80, end='\r') 
                    print("[TTS] Playback of current utterance finished/interrupted.")

                    self.llm_to_tts_queue.task_done() # Mark task as done for the queue
                
            except queue.Empty:
                time.sleep(0.05) # Short sleep if queue is empty to prevent tight looping
            except Exception as e:
                print(f"[TTS] Unexpected error in TTS playback loop: {e}")
                time.sleep(0.1) # Prevent tight looping on continuous errors
        print("[TTS] Player finished.")