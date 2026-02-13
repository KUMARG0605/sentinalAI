
import os
import sys
import threading
import time
from pathlib import Path

# Add project root to sys.path to ensure imports work
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from app.src.rag import load_essentials, ask, stop_llama_server
from app.src.stt import transcribe_mic

def run_voice_pipeline():
    print("Initializing Voice Pipeline...")
    retriever, llm, server_proc = load_essentials()
    
    try:
        print("\n" + "="*50)
        print("Voice Pipeline Ready!")
        print("Speak 'exit', 'stop', or 'quit' to end the session.")
        print("="*50 + "\n")
        
        while True:
            # 1. Listen (STT)
            print("\nWaiting for voice input...")
            user_query = transcribe_mic()
            
            if not user_query:
                print("No speech detected. Listening again...")
                continue
                
            print(f"\nUser said: {user_query}")
            
            # Check for exit commands
            if user_query.lower().strip() in ["exit", "stop", "quit", "bye", "goodbye"]:
                print("Exiting voice pipeline...")
                break
            
            # 2. Process & Respond (RAG + TTS)
            # rag.ask() handles retrieval, LLM generation, and TTS playback
            ask(user_query, retriever, llm)
            
            # Small pause before listening again to avoid picking up TTS echo if using speakers
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as e:
        print(f"\nError in pipeline: {e}")
    finally:
        print("\nShutting down resources...")
        stop_llama_server(server_proc)
        print("Done.")

if __name__ == "__main__":
    run_voice_pipeline()
