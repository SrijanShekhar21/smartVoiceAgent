import queue
import threading
import time
import os
import requests
import subprocess
import json 
import re # For sentence splitting

class TTSPlayer:
    def __init__(self, llm_to_tts_queue: queue.Queue,
                 interrupt_bot_event: threading.Event,
                 bot_speaking_event: threading.Event,
                 exit_event: threading.Event):
        
        self.llm_to_tts_queue = llm_to_tts_queue
        self.interrupt_bot_event = interrupt_bot_event
        self.bot_speaking_event = bot_speaking_event
        self.exit_event = exit_event

        self.current_llm_utterance_chunks = [] # To accumulate chunks for TTS
        self.current_player_process = None # To store ffplay process for interruption

    DEFAULT_VOICE_ID = "Melody"  # Example VoiceId, change as needed (or "Will" as in the commented out code)
    DEFAULT_SPEED = 0.8  # Range: -1.0 to 1.0 (0 is normal)
    DEFAULT_PITCH = 0  # Range: -0.5 to 0.5 (0 is normal)
    foundPunctuation = False # Flag to track if punctuation is found in the current segment

    def simulate_speech(self, text: str):
        words = text.split()
        print("[TTS-Bot speaking simulation: ] ", end="", flush=True)
        for word in words:
            print(word, end=" ", flush=True)
            time.sleep(0.1)  # Simulate a short delay for each word

    def _play_audio_stream(self, response_stream, text_for_logging: str):
        """
        Helper to play the audio stream from Unreal Speech, handling ffplay and interruptions.
        This is extracted logic from synthesize_speech_v8 to be reusable.
        """
        player_command = ["ffplay", "-autoexit", "-", "-nodisp", "-loglevel", "quiet"]
        player_process = subprocess.Popen(
            player_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.current_player_process = player_process # Store reference to enable external interruption

        start_time = time.time()
        first_byte_time = None
        
        try:
            for chunk in response_stream.iter_content(chunk_size=1024):
                # Check for interruption *while* streaming
                if self.interrupt_bot_event.is_set():
                    print(f"\n[TTS] Playback interrupted for text: '{text_for_logging[:50]}...'")
                    player_process.kill() # Immediately stop ffplay
                    self.interrupt_bot_event.clear() # Clear the event for the next turn
                    break # Exit the chunk processing loop

                if chunk:
                    if first_byte_time is None:
                        first_byte_time = time.time()
                        ttfb = int((first_byte_time - start_time) * 1000)
                        print(f"[TTS] Time to First Byte (TTFB): {ttfb}ms for text: '{text_for_logging[:50]}...'")
                    player_process.stdin.write(chunk)
                    player_process.stdin.flush()
        except requests.exceptions.RequestException as e:
            print(f"[TTS] Request failed during streaming: {e}")
            if player_process.poll() is None: # If player is still running
                player_process.kill()
            raise
        finally:
            if player_process.stdin:
                try:
                    player_process.stdin.close()
                except BrokenPipeError:
                    # This can happen if ffplay exits early (e.g., due to an error or user closing it)
                    pass
            player_process.wait() # Wait for ffplay to finish
            self.current_player_process = None # Clear reference
            print(f"[TTS] Finished playing speech for text: '{text_for_logging[:50]}...'")


    def synthesize_speech_v8(self, text: str, voice_id: str = None, speed: float = None, pitch: float = None):
        """
        Generates speech from text using Unreal Speech API and plays it.

        Args:
            text (str): The text to synthesize.
            voice_id (str, optional): The VoiceId to use. Defaults to self.DEFAULT_VOICE_ID.
            speed (float, optional): Speech speed. Defaults to None (API default or class default if set).
            pitch (float, optional): Speech pitch. Defaults to None (API default or class default if set).
        """
        # Set bot speaking event before API call
        self.bot_speaking_event.set()

        UNREAL_SPEECH_API_KEY = os.getenv("UNREAL_SPEECH_API_KEY")

        if not UNREAL_SPEECH_API_KEY:
            # Unset bot_speaking_event on error
            self.bot_speaking_event.clear() 
            raise ValueError("UNREAL_SPEECH_API_KEY environment variable not set.")

        UNREAL_SPEECH_STREAM_URL = "https://api.v8.unrealspeech.com/stream"

        headers = {
            "Authorization": f"Bearer {UNREAL_SPEECH_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "Text": text,
            "VoiceId": voice_id if voice_id is not None else self.DEFAULT_VOICE_ID,
            "speed": speed if speed is not None else self.DEFAULT_SPEED,
            "pitch": pitch if pitch is not None else self.DEFAULT_PITCH,
        }

        try:
            with requests.post(UNREAL_SPEECH_STREAM_URL, stream=True, headers=headers, json=payload, timeout=30) as r:
                if r.status_code != 200:
                    error_message = f"Unreal Speech API Error: {r.status_code}"
                    try:
                        error_detail = r.json()
                        error_message += f" - {error_detail.get('message', r.text)}"
                    except json.JSONDecodeError:
                        error_message += f" - {r.text}"
                    
                    # Unset bot_speaking_event on error
                    self.bot_speaking_event.clear()
                    raise requests.exceptions.HTTPError(error_message, response=r)

                self.simulate_speech(text)  # Simulate speech output in console for debugging
                print()  # For clarity in console output
                # Now use the helper function to play the stream
                # self._play_audio_stream(r, text) # Pass the response object and original text for logging  <----- Uncomment to play TTS audio

        except requests.exceptions.RequestException as e:
            print(f"[TTS] Request failed for text: '{text[:50]}...': {e}")
            # Unset bot_speaking_event on error
            self.bot_speaking_event.clear()
            raise
        finally:
            # Ensure bot_speaking_event is cleared *after* playback or interruption
            if self.bot_speaking_event.is_set():
                self.bot_speaking_event.clear()


    def play_tts(self):
        """
        Continuously pulls text chunks from queue, accumulates them,
        and triggers audio playback for meaningful segments.
        Includes interruption logic. This function is designed to be run in a separate thread.
        """
        print("[TTS] Player ready.")
        while not self.exit_event.is_set():
            try:
                # Use get() with a timeout to allow loop to check exit_event periodically
                item = self.llm_to_tts_queue.get(timeout=0.1) 
                
                if item["type"] == "start_response":
                    self.current_llm_utterance_chunks = []
                    print("[TTS] Starting new LLM utterance...")

                elif item["type"] == "chunk":
                    chunk_text = item["text"]
                    self.current_llm_utterance_chunks.append(chunk_text)

                    # Accumulate text and check for natural breakpoints or length
                    combined_text = "".join(self.current_llm_utterance_chunks).strip()
                    # print("[TTS] Accumulated text chunk:", combined_text, "...\n") # Print first 50 chars for debugging

                    segment_to_synthesize = ""
                    remaining_text = ""
                    
                    "Dinosaurs are fascinating creatures that roamed the Earth millions of years ago. Their diverse sizes and shapes"

                    words_in_combined_text = combined_text.split()
                    for word in words_in_combined_text:
                        last_character = word[-1]
                        segment_to_synthesize += word + " "
                        if last_character in {'.', '?', '!', ':', ';'}:  # Check for sentence-ending punctuation
                            self.foundPunctuation = True
                            break
                    
                    if not self.foundPunctuation:
                        segment_to_synthesize = ""
                        remaining_text = combined_text  # If no punctuation found, keep accumulating
                    else:
                        for word in words_in_combined_text[len(segment_to_synthesize.split()):]:
                            remaining_text += word + " "
                            self.foundPunctuation = False  # Reset for next segment

                    # print(f"[TTS] Synthesize segment: '{segment_to_synthesize}...'")
                    # print(f"[TTS] Remaining text for next segment: '{remaining_text}...'")

                    if segment_to_synthesize:
                        try:
                            self.synthesize_speech_v8(segment_to_synthesize)
                        except Exception as e:
                            print(f"[TTS] Error during speech synthesis for segment: '{segment_to_synthesize}...': {e}")
                            # The synthesize_speech_v8 should clear bot_speaking_event on error,
                            # but ensuring it here too for robustness.
                            self.bot_speaking_event.clear()

                        # Reset current chunks to remaining text for next iteration
                    self.current_llm_utterance_chunks = [remaining_text] if remaining_text else []

                elif item["type"] == "end_response":
                    # If there are any remaining chunks after the LLM stream ends, synthesize them
                    if self.current_llm_utterance_chunks:
                        final_segment = "".join(self.current_llm_utterance_chunks).strip()
                        if final_segment:
                            print(f"[TTS] Synthesizing final segment: '{final_segment}'")
                            try:
                                self.synthesize_speech_v8(final_segment)
                            except Exception as e:
                                print(f"[TTS] Error during final speech synthesis for segment: '{final_segment[:50]}...': {e}")
                                self.bot_speaking_event.clear()
                        self.current_llm_utterance_chunks = [] # Clear for next LLM turn
                    print("[TTS] Finished LLM utterance.")
                    self.bot_speaking_event.clear() # Ensure bot_speaking_event is cleared after entire utterance
                
                self.llm_to_tts_queue.task_done() # Mark task as done for the queue

            except queue.Empty:
                # If queue is empty, check for interruption here too, in case a speech segment is very short
                # or there's a long pause before the next LLM response.
                if self.interrupt_bot_event.is_set():
                    if self.current_player_process and self.current_player_process.poll() is None:
                        # Only kill if a process is actually running (not already dead)
                        print("\n[TTS] Interruption detected while idle, killing active player process if any.")
                        self.current_player_process.kill()
                    self.interrupt_bot_event.clear()
                    self.bot_speaking_event.clear() # Ensure event is cleared if it was somehow left set
                
                time.sleep(0.05) # Short sleep if queue is empty to prevent tight looping
            except Exception as e:
                print(f"[TTS] Unexpected error in TTS playback loop: {e}")
                self.bot_speaking_event.clear() # Ensure event is cleared on error
                time.sleep(0.1) # Prevent tight looping on continuous errors
        print("[TTS] Player finished.")