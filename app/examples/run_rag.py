
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.src.rag import load_essentials, ask, stop_llama_server


def main():
    print("Loading models...")
    retriever, llm, server_proc = load_essentials()
    
    try:
        
        questions = [
            "explain the hierarchial clustering",
        ]
        
        for question in questions:
            print(f"\n{'='*60}")
            print(f"Question: {question}")
            print("="*60)
            ask(question, retriever, llm, language="telugu")
            
    finally:
        stop_llama_server(server_proc)
        print("\nLlama server stopped successfully.")


if __name__ == "__main__":
    main()
