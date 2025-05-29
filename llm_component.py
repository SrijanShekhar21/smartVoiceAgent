import asyncio
import os
import queue
import threading
import google.generativeai as genai # New import for Gemini

class LLMProcessor:
    def __init__(self, stt_to_llm_queue: queue.Queue,
                 llm_to_tts_queue: queue.Queue,
                 exit_event: threading.Event):
        
        self.stt_to_llm_queue = stt_to_llm_queue
        self.llm_to_tts_queue = llm_to_tts_queue
        self.exit_event = exit_event

        # Configure Gemini Client (should be done once)
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

    async def _get_gemini_response_async(self, prompt: str) -> str:
        """
        Sends the user's prompt to the Gemini LLM and returns its response.
        """
        print(f"\n[LLM] Sending to Gemini: '{prompt}'")
        try:
            model = genai.GenerativeModel('gemini-2.0-flash') # You can choose other Gemini models
            response = await model.generate_content_async(prompt) # Use async method
            
            # Accessing text from the response, handling potential empty text or safety settings
            if response.text:
                llm_response = response.text
                print(f"[LLM] Gemini Response: {llm_response}")
                return llm_response
            else:
                print("[LLM] Gemini returned an empty response or blocked content.")
                return "I'm sorry, I couldn't generate a response for that."
        except Exception as e:
            print(f"[LLM] Error calling Gemini API: {e}")
            return "I'm sorry, I encountered an error when thinking. Please try again."

    async def process_llm_requests(self):
        """
        Continuously pulls user sentences from queue, sends to LLM, and puts responses on TTS queue.
        This function is designed to be run in a separate thread with its own asyncio loop.
        """
        print("[LLM] Processor ready.")
        while not self.exit_event.is_set():
            try:
                # Use get() with a timeout to allow loop to check exit_event periodically
                user_sentence = self.stt_to_llm_queue.get(timeout=0.1) # Blocks for up to 0.1s
                
                if user_sentence:
                    llm_response = await self._get_gemini_response_async(user_sentence)
                    try:
                        self.llm_to_tts_queue.put_nowait(llm_response) # Use put_nowait to avoid blocking
                    except queue.Full:
                        print("[LLM] TTS queue is full, skipping LLM response for TTS.")
                self.stt_to_llm_queue.task_done() # Mark task as done for the queue
            except queue.Empty:
                # If queue is empty, yield control to the asyncio event loop
                await asyncio.sleep(0.05) 
            except Exception as e:
                print(f"[LLM] Unexpected error in LLM processing loop: {e}")
                await asyncio.sleep(0.1) # Prevent tight looping on continuous errors
        print("[LLM] Processor finished.")