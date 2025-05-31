import asyncio
import os
import queue
import threading
import google.generativeai as genai # New import for Gemini
from google import genai
from google.genai import types

class LLMProcessor:
    def __init__(self, stt_to_llm_queue: queue.Queue,
                 llm_to_tts_queue: queue.Queue,
                 exit_event: threading.Event):
        
        self.stt_to_llm_queue = stt_to_llm_queue
        self.llm_to_tts_queue = llm_to_tts_queue
        self.exit_event = exit_event

        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.chat = self.client.chats.create(model="gemini-2.0-flash")

    async def _get_gemini_response_async(self, prompt: str) -> str:
        """
        Sends the user's prompt to the Gemini LLM and returns its response.
        """
        # Combine system prompt with user prompt    

        print(f"\n[LLM] Sending to Gemini: '{prompt}'")
        try:
            response = self.chat.send_message(
                prompt
            ) # Use async method for streaming response

            if response is None:
                print("[LLM] Gemini returned None response.")
                return "I'm sorry, I couldn't generate a response for that."
            
            llm_response = response.text # Get the text from the response
            print(f"[LLM] Gemini Response: {llm_response}")
            return llm_response

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