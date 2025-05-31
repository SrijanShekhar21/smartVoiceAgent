import asyncio
import os
import queue
import threading
import google.generativeai as genai 
from google import genai
from google.genai import types

class LLMProcessor:
    def __init__(self, stt_to_llm_queue: queue.Queue,
                 llm_to_tts_queue: queue.Queue,
                 exit_event: threading.Event):
        
        self.stt_to_llm_queue = stt_to_llm_queue
        self.llm_to_tts_queue = llm_to_tts_queue
        self.exit_event = exit_event

        self.system_instructions = (
            "You are a helpful and articulate AI assistant designed for real-time voice conversations. "
            "Your responses must be highly suitable for Text-to-Speech (TTS) conversion. "
            "Make sure to keep your senteces of length about 12-15 words and end them with a fullstop"
            "Therefore, use only plain, natural language. "
            "Avoid all Markdown formatting (e.g., bold, italics, code blocks, bullet points, numbered lists). "
            "Do not use emojis or other non-standard symbols. "
            "Spell out numbers, abbreviations, and acronyms clearly if they might be ambiguous when spoken "
            "(e.g., 'NASA' should be 'N.A.S.A.' or 'National Aeronautics and Space Administration' depending on context, "
            "'$5' should be 'five dollars'). "
            "Ensure your sentences are grammatically correct and use standard punctuation that aids natural cadence. "
            "Keep your responses concise and to the point, while remaining informative."
        )

        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.chat = self.client.chats.create(model="gemini-2.0-flash", 
                                             config=types.GenerateContentConfig(system_instruction=self.system_instructions)
                                            )             
                                            
        # Keep track of the full response for potential logging or later use
        self.full_llm_response_text = ""

    async def _get_gemini_response_async(self, prompt: str) -> str:
        """
        Sends the user's prompt to the Gemini LLM, sends chunks to TTS queue,
        and returns the full combined response text.
        """
        self.full_llm_response_text = "" # Reset for new response

        print(f"\n[LLM] Sending to Gemini: '{prompt}'")
        try:
            # Signal the start of a new LLM response (important for TTS consumer)
            # Use asyncio.to_thread because queue.put_nowait() is blocking in an async context
            await asyncio.to_thread(self.llm_to_tts_queue.put_nowait, {"type": "start_response"})

            response = self.chat.send_message_stream(prompt)

            for chunk in response:
                if chunk.text:
                    llm_chunk = chunk.text
                    self.full_llm_response_text += llm_chunk
                    print(f"[LLM] Gemini Chunk: {llm_chunk}", end="") # Print chunk as it arrives
                    print()
                    
                    # Put each chunk onto the TTS queue
                    # Using put_nowait to avoid blocking, the queue has a maxsize
                    try:
                        await asyncio.to_thread(self.llm_to_tts_queue.put_nowait, {"type": "chunk", "text": llm_chunk})
                    except queue.Full:
                        print("[LLM] TTS queue is full, dropping LLM chunk.")
            
            # Signal the end of the LLM response
            await asyncio.to_thread(self.llm_to_tts_queue.put_nowait, {"type": "end_response"})
            print("\n[LLM] End of Gemini Response.")
            return self.full_llm_response_text # Return full text for logging/other purposes
            
        except Exception as e:
            print(f"[LLM] Error calling Gemini API: {e}")
            error_msg = "I'm sorry, I encountered an error when thinking. Please try again."
            # Optionally put an error message chunk or end signal
            try:
                await asyncio.to_thread(self.llm_to_tts_queue.put_nowait, {"type": "chunk", "text": error_msg})
                await asyncio.to_thread(self.llm_to_tts_queue.put_nowait, {"type": "end_response"})
            except queue.Full:
                print("[LLM] TTS queue full, could not send error message.")
            return error_msg

    async def process_llm_requests(self):
        """
        Continuously pulls user sentences from queue and triggers LLM processing.
        The LLM method now handles putting responses on the TTS queue directly.
        This function is designed to be run in a separate thread with its own asyncio loop.
        """
        print("[LLM] Processor ready.")
        while not self.exit_event.is_set():
            try:
                # Use get() with a timeout to allow loop to check exit_event periodically
                # Wrap in asyncio.to_thread as queue.get is blocking and this is an async method
                user_sentence = await asyncio.to_thread(self.stt_to_llm_queue.get, timeout=0.1)
                
                if user_sentence:
                    print(f"[LLM] Processing user input: '{user_sentence}'")
                    # _get_gemini_response_async now directly sends chunks to llm_to_tts_queue
                    # It still returns the full text which you can use for logging or other purposes
                    await self._get_gemini_response_async(user_sentence)
                
                # Mark task as done for the queue
                await asyncio.to_thread(self.stt_to_llm_queue.task_done)

            except queue.Empty:
                # If queue is empty, yield control to the asyncio event loop
                await asyncio.sleep(0.05) 
            except Exception as e:
                print(f"[LLM] Unexpected error in LLM processing loop: {e}")
                await asyncio.sleep(0.1) # Prevent tight looping on continuous errors
        print("[LLM] Processor finished.")