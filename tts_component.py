import queue
import threading
import time

class TTSPlayer:
    def __init__(self, llm_to_tts_queue: queue.Queue,
                 interrupt_bot_event: threading.Event,
                 bot_speaking_event: threading.Event,
                 exit_event: threading.Event):
        
        self.llm_to_tts_queue = llm_to_tts_queue
        self.interrupt_bot_event = interrupt_bot_event
        self.bot_speaking_event = bot_speaking_event
        self.exit_event = exit_event

    def play_tts(self):
        """
        Continuously pulls text from queue and simulates audio playback.
        Includes interruption logic. This function is designed to be run in a separate thread.
        """
        print("[TTS] Player ready.")
        while not self.exit_event.is_set():
            try:
                # Blocking get with timeout to allow checking exit_event periodically
                text_to_speak = self.llm_to_tts_queue.get(timeout=0.1) 
                
                if text_to_speak:
                    print(f"\n[TTS] Bot speaking: '{text_to_speak}'")
                    
                    self.bot_speaking_event.set() # Signal that bot is now speaking
                    
                    # --- Simulate Audio Playback with Interruption Check ---
                    # In a real scenario, this would involve your actual TTS library
                    # and PyAudio stream playback. This loop would be replaced by
                    # chunks of audio being played, with checks for interruption.
                    words = text_to_speak.split()
                    for i, word in enumerate(words):
                        if self.exit_event.is_set(): # Check for global exit signal
                            break # Stop playback if main program is exiting
                        if self.interrupt_bot_event.is_set(): # Check for user interruption
                            print("\n[TTS] Playback interrupted by user.")
                            self.interrupt_bot_event.clear() # Clear the interruption signal
                            break # Stop playing current sentence

                        # Simulate playing each word
                        # In real TTS, you might get audio chunks to play.
                        # This 'sleep' time would be determined by the actual audio duration.
                        print(f"  {word}", end=' ', flush=True) # flush=True to ensure immediate print
                        time.sleep(0.08) # Simulate speaking duration for a word

                    print("") # Newline after simulated speaking for neatness

                    self.bot_speaking_event.clear() # Signal that bot finished speaking (or was interrupted)
                
                self.llm_to_tts_queue.task_done() # Mark task as done for the queue
            except queue.Empty:
                time.sleep(0.05) # Short sleep if queue is empty to prevent tight looping
            except Exception as e:
                print(f"[TTS] Unexpected error in TTS playback loop: {e}")
                time.sleep(0.1) # Prevent tight looping on continuous errors
        print("[TTS] Player finished.")