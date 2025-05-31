import queue
import threading
import time
from pydub import AudioSegment
from pydub.playback import play # pydub.playback relies on PyAudio usually
from unrealspeech import UnrealSpeechAPI, play
import os

class TTSPlayer:
    def __init__(self, llm_to_tts_queue: queue.Queue,
                 interrupt_bot_event: threading.Event,
                 bot_speaking_event: threading.Event,
                 exit_event: threading.Event):
        
        self.llm_to_tts_queue = llm_to_tts_queue
        self.interrupt_bot_event = interrupt_bot_event
        self.bot_speaking_event = bot_speaking_event
        self.exit_event = exit_event
    
    def break_sentence_into_chunks(self, sentence, words_per_chunk=8):        
        # 1. Split the sentence into a list of words
        # .split() without arguments handles multiple spaces correctly
        words = sentence.split()
        num_words = len(words)

        if num_words == 0:
            print("The sentence is empty.")
            return []

        array_of_chunks = []

        for i in range(0, num_words, words_per_chunk):
            chunk = words[i : i + words_per_chunk]
            chunk_sentence = " ".join(chunk)
            array_of_chunks.append(chunk_sentence)
        
        return array_of_chunks
    
    def synthesize_speech(self, text):
        api_key = os.getenv("UNREAL_SPEECH_API_KEY")
        if not api_key:
            raise ValueError("UNREAL_SPEECH_API_KEY environment variable is not set.")
        
        generate = UnrealSpeechAPI(api_key)
        
        # Generate audio stream
        audio_stream = generate.stream(
            text=text,
            voice_id="Will"  # Example voice ID, change as needed
        )

        play(audio_stream)  

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
                    if self.exit_event.is_set():
                        print("[TTS] Exit event set, stopping playback.")
                        break
                    if self.interrupt_bot_event.is_set():
                        print("\n[TTS] Playback interrupted by user.")
                        self.interrupt_bot_event.clear()    
                        break

                    self.synthesize_speech(text_to_speak)
                    # --- Synthesize Speech using chunking---
                    # array_of_chunks = self.break_sentence_into_chunks(text_to_speak, words_per_chunk=10)
                    # # Iterate over each chunk to simulate speaking
                    # for chunk in array_of_chunks:
                    #     if self.exit_event.is_set():
                    #         break
                    #     if self.interrupt_bot_event.is_set():
                    #         print("\n[TTS] Playback interrupted by user.")
                    #         self.interrupt_bot_event.clear()
                    #         break
                        
                    #     self.synthesize_speech(chunk)  # Call the synthesis function which uses audio streaming

                    print("Bot speaking complete, listening for user input...") # Newline after simulated speaking for neatness
                    self.bot_speaking_event.clear() # Signal that bot finished speaking (or was interrupted)
                
                self.llm_to_tts_queue.task_done() # Mark task as done for the queue
            except queue.Empty:
                time.sleep(0.05) # Short sleep if queue is empty to prevent tight looping
            except Exception as e:
                print(f"[TTS] Unexpected error in TTS playback loop: {e}")
                time.sleep(0.1) # Prevent tight looping on continuous errors
        print("[TTS] Player finished.")

