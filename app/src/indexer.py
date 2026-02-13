import os
import torch
import json
from typing import List, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    UnstructuredWordDocumentLoader,
    UnstructuredPowerPointLoader,
)

from pdf2image import convert_from_path
from PIL import Image
import pytesseract
import cv2
import numpy as np
import dotenv

dotenv.load_dotenv()
torch.set_num_threads(os.cpu_count())

# Supported file extensions
EXTENSIONS = (
    ".pdf", ".doc", ".docx",
    ".ppt", ".pptx", ".md", ".txt"
)

# User folders to scan when indexing from C:/
USER_FOLDERS = [
    "Desktop",
    "Documents",
    "Pictures",
    "Images",
    "OneDrive",
    "Downloads",
    "Videos",
    "Music"
]


def get_user_profile_paths() -> List[str]:
    """Get all user profile paths on the system."""
    user_profiles = []
    
    current_user = os.path.expanduser("~")
    if os.path.exists(current_user):
        user_profiles.append(current_user)
    
    users_dir = "C:/Users"
    if os.path.exists(users_dir):
        try:
            for item in os.listdir(users_dir):
                profile_path = os.path.join(users_dir, item)
                if os.path.isdir(profile_path) and item not in ["Public", "Default", "Default User", "All Users"]:
                    if profile_path not in user_profiles:
                        user_profiles.append(profile_path)
        except PermissionError:
            pass
    
    return user_profiles


def get_user_data_folders(root_path: str) -> List[str]:
    """Get list of folders to scan based on root path."""
    if os.path.normpath(root_path).upper() in ["C:\\", "C:", "C:/"]:
        print(" Detected C:/ root - scanning only user data folders")
        
        folders_to_scan = []
        user_profiles = get_user_profile_paths()
        
        print(f" Found {len(user_profiles)} user profile(s)")
        
        for profile in user_profiles:
            for folder_name in USER_FOLDERS:
                folder_path = os.path.join(profile, folder_name)
                if os.path.exists(folder_path):
                    folders_to_scan.append(folder_path)
                    print(f"  Will scan: {folder_path}")
        
        return folders_to_scan if folders_to_scan else [root_path]
    
    return [root_path]


def should_exclude_path(path: str, exclude_paths: List[str]) -> bool:
    """Check if a path should be excluded from scanning."""
    if not exclude_paths:
        return False
    
    path_lower = path.lower()
    for exclude in exclude_paths:
        exclude_lower = exclude.lower()
        if exclude_lower in path_lower:
            return True
        path_parts = path.split(os.sep)
        if exclude in path_parts or exclude_lower in [p.lower() for p in path_parts]:
            return True
    return False


def load_checkpoint(checkpoint_path: str) -> Set[str]:
    """Load the set of processed files from the checkpoint file."""
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"Warning: Strings loading checkpoint: {e}")
    return set()


def save_checkpoint(checkpoint_path: str, processed_files: Set[str]):
    """Save the set of processed files to the checkpoint file."""
    try:
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(list(processed_files), f, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save checkpoint: {e}")


def collect_files(root_path: str, exclude_paths: List[str] = None, processed_files: Set[str] = None) -> List[str]:
    """Collect all supported files from the root path, skipping processed ones."""
    if exclude_paths is None:
        exclude_paths = []
    if processed_files is None:
        processed_files = set()
    
    folders_to_scan = get_user_data_folders(root_path)
    files = []
    
    print("Scanning folders for new files...")
    for scan_path in folders_to_scan:
        for root, dirs, filenames in os.walk(scan_path):
            if exclude_paths:
                dirs[:] = [d for d in dirs if not should_exclude_path(os.path.join(root, d), exclude_paths)]
            
            for f in filenames:
                if f.lower().endswith(EXTENSIONS):
                    full_path = os.path.join(root, f)
                    if full_path not in processed_files:
                        files.append(full_path)
    
    return files


def preprocess_for_ocr(image: Image.Image) -> Image.Image:
    """Preprocess image for better OCR accuracy."""
    img = np.array(image)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    return Image.fromarray(thresh)


def load_image_with_ocr(path: str) -> Document:
    """Load an image and extract text using OCR."""
    try:
        text = pytesseract.image_to_string(
            preprocess_for_ocr(Image.open(path)),
            config="--psm 6"
        )
        
        return Document(
            page_content=text if text.strip() else "[IMAGE – NO OCR TEXT]",
            metadata={
                "source": path,
                "type": "image",
                "page": 1,
                "ocr_empty": not bool(text.strip()),
            }
        )
    except Exception as e:
        print(f"OCR failed for image {path}: {e}")
        return None


def load_pdf_with_ocr_fallback(pdf_path: str) -> List[Document]:
    """Load PDF with OCR fallback for scanned documents."""
    docs = []

    try:
        loader = PyPDFLoader(pdf_path)
        text_docs = loader.load()
        total_text = sum(len(d.page_content.strip()) for d in text_docs)

        if total_text > 100:
            for d in text_docs:
                d.metadata["type"] = "pdf"
                d.metadata["source"] = pdf_path
            return text_docs

        print(f" OCR fallback for scanned PDF: {pdf_path}")
        images = convert_from_path(pdf_path, dpi=300)

        for page_num, image in enumerate(images, start=1):
            text = pytesseract.image_to_string(
                preprocess_for_ocr(image),
                config="--psm 6"
            )

            docs.append(
                Document(
                    page_content=text if text.strip() else "[SCANNED PAGE – NO OCR TEXT]",
                    metadata={
                        "source": pdf_path,
                        "type": "ocr_pdf",
                        "page": page_num,
                        "ocr_empty": not bool(text.strip()),
                    }
                )
            )
    except Exception as e:
         print(f"Error loading PDF {pdf_path}: {e}")

    return docs


def load_document(path: str) -> List[Document]:
    """Load a single document based on its extension."""
    documents = []
    ext = path.lower()

    try:
        if ext.endswith(".pdf"):
            documents.extend(load_pdf_with_ocr_fallback(path))

        elif ext.endswith((".txt", ".md")):
            loader = TextLoader(path, encoding="utf-8")
            docs = loader.load()
            for d in docs:
                d.metadata.update({"source": path, "type": "text", "page": 1})
            documents.extend(docs)

        elif ext.endswith((".docx", ".doc")):
            loader = UnstructuredWordDocumentLoader(path, mode="elements")
            docs = loader.load()
            for i, d in enumerate(docs, start=1):
                d.metadata.update({"source": path, "type": "docx", "page": i})
            documents.extend(docs)

        elif ext.endswith((".pptx", ".ppt")):
            loader = UnstructuredPowerPointLoader(path, mode="elements")
            docs = loader.load()
            for d in docs:
                d.metadata.update({
                    "source": path,
                    "type": "pptx",
                    "page": d.metadata.get("page_number", 1)
                })
            documents.extend(docs)

        elif ext.endswith((".jpg", ".jpeg", ".png")):
            doc = load_image_with_ocr(path)
            if doc:
                documents.append(doc)

    except Exception as e:
        print(f" Error loading {path}: {e}")

    return documents


def process_single_file(file_path: str, embeddings) -> FAISS:
    """Process a single file and create a FAISS vectorstore."""
    try:
        # print(f" Loading: {os.path.basename(file_path)}") # Reduce noise
        docs = load_document(file_path)
        
        if not docs:
            # print(f" No documents loaded from: {os.path.basename(file_path)}")
            return None
        
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        chunks = splitter.split_documents(docs)
        
        # Filter valid chunks
        valid_chunks = []
        for chunk in chunks:
            try:
                if not hasattr(chunk, 'page_content'):
                    continue
                content = chunk.page_content
                if not isinstance(content, str) or not content.strip():
                    continue
                stripped = content.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    continue
                if len(stripped) < 3 or not any(c.isalnum() for c in stripped):
                    continue
                valid_chunks.append(chunk)
            except:
                continue
        
        if not valid_chunks:
            # print(f" No valid chunks from: {os.path.basename(file_path)}")
            return None
        
        # print(f" Embedding {len(valid_chunks)} chunks from: {os.path.basename(file_path)}")
        vectorstore = FAISS.from_documents(valid_chunks, embeddings)
        
        return vectorstore
        
    except Exception as e:
        print(f" Error processing {os.path.basename(file_path)}: {e}")
        return None


def create_index(data_folder: str, index_path: str = "faiss_index", exclude_paths: List[str] = None,
                 max_workers: int = 4, embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2", batch_size: int = 10):
    
    checkpoint_path = os.path.join(os.path.dirname(index_path) if os.path.dirname(index_path) else ".", "processed_files.json")
    
    if exclude_paths:
        print(f" Custom exclusions: {', '.join(exclude_paths)}")
    
    processed_files = load_checkpoint(checkpoint_path)
    print(f" Loaded checkpoint: {len(processed_files)} files already processed.")

    print(" Collecting files...")
    files_to_process = collect_files(data_folder, exclude_paths, processed_files)
    total_files = len(files_to_process)
    print(f" Found {total_files} new files to process.")
    
    if not files_to_process:
        print(" No new files found to process!")
        return
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f" Using device: {device}")
    
    print(" Loading embedding model...")
    embeddings = HuggingFaceEmbeddings(
        model_name=embedding_model,
        model_kwargs={"local_files_only": False, "device": device},
        encode_kwargs={
            "batch_size": 128 if device == "cuda" else 32,
            "normalize_embeddings": True,
        },
    )

    # Load existing index if available
    main_vectorstore = None
    if os.path.exists(index_path):
        try:
            print(f" Loading existing index from {index_path}...")
            main_vectorstore = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
            print(" Existing index loaded.")
        except Exception as e:
            print(f" Warning: Could not load existing index: {e}. Starting fresh.")
    
    if main_vectorstore is None and processed_files:
         print(" Checkpoint exists but no index found. Re-indexing processed files is recommended to ensure consistency.")
         # Optional: You might want to clear processed_files here if the index is missing, 
         # but for now we follow the user's request to "resume", assuming the index might have been moved or we just add to new.
         # A safer approach if index is missing is to start over.
         # For this implementation, we will start a new index for the new files.
    
    print(f" Processing {total_files} files with {max_workers} workers in batches of {batch_size}...")
    
    # Process in batches
    for i in range(0, total_files, batch_size):
        batch_files = files_to_process[i : i + batch_size]
        batch_vectorstores = []
        
        print(f"\n--- Processing Batch {i // batch_size + 1}/{(total_files + batch_size - 1) // batch_size} ({len(batch_files)} files) ---")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {
                executor.submit(process_single_file, file_path, embeddings): file_path
                for file_path in batch_files
            }
            
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                try:
                    vectorstore = future.result()
                    if vectorstore:
                        batch_vectorstores.append(vectorstore)
                        print(f" [OK] {os.path.basename(file_path)}")
                    else:
                        print(f" [SKIP] {os.path.basename(file_path)} (empty/invalid)")
                    
                    # Mark as processed regardless of success to avoid infinite retry on bad files
                    processed_files.add(file_path)
                    
                except Exception as e:
                    print(f" [FAIL] {os.path.basename(file_path)}: {e}")
                    # Optionally don't mark as processed if you want to retry later, 
                    # but usually it's better to skip to avoid getting stuck.
                    processed_files.add(file_path) 

        # Merge batch results
        if batch_vectorstores:
            if main_vectorstore is None:
                main_vectorstore = batch_vectorstores[0]
                for vs in batch_vectorstores[1:]:
                    main_vectorstore.merge_from(vs)
            else:
                for vs in batch_vectorstores:
                    main_vectorstore.merge_from(vs)
            
            # Save checkpoint after every batch
            print(f" Saving index and checkpoint...")
            main_vectorstore.save_local(index_path)
            save_checkpoint(checkpoint_path, processed_files)
        else:
             # Even if no vectorstores were created (all empty files), save the checkpoint
             save_checkpoint(checkpoint_path, processed_files)

    print("\n All processing complete!")
    if main_vectorstore:
        print(f" Final index saved at: {index_path}")
    print(f" Processed files log updated at: {checkpoint_path}")


if __name__ == "__main__":
    create_index("C:/", exclude_paths=["eye_disease_detection", "node_modules", ".git", "__pycache__"])
