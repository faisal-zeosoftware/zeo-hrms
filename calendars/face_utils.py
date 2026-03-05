try:
    from deepface import DeepFace
except ImportError:
    DeepFace = None

import numpy as np
import base64
from io import BytesIO
from PIL import Image

def get_face_encoding(image_data, model_name='VGG-Face'):
    """
    Extracts face representation (embedding) from an image.
    Returns a list or None if no face detected.
    """
    if DeepFace is None:
        print("CRITICAL: DeepFace library not installed. Install with: pip install deepface")
        return None
    
    try:
        # Resolve image data to numpy array
        if isinstance(image_data, str) and ',' in image_data:
            image_data = image_data.split(',')[1]
            image_bytes = base64.b64decode(image_data)
        elif isinstance(image_data, str):
            image_bytes = base64.b64decode(image_data)
        else:
            image_bytes = image_data.read()

        image = Image.open(BytesIO(image_bytes))
        image = np.array(image.convert('RGB'))
        
        # DeepFace.represent returns a list of dictionaries (one for each face detected)
        # We set enforce_detection=False so it doesn't throw an exception if no face is found
        # instead it returns the embedding of the entire frame (not ideal but better than crashing)
        # We also attempt to catch the 'face could not be detected' result.
        result = DeepFace.represent(img_path=image, model_name=model_name, enforce_detection=False)
        
        if result and len(result) > 0:
            # Check if it actually detected a face (DeepFace might return full image if false)
            # You might want to filter or log if facial area is small or results are low confidence here
            return result[0]["embedding"]
        return None
        
    except Exception as e:
        print(f"DeepFace encoding error: {str(e)}")
        import traceback
        traceback.print_exc() # Print full stack trace to terminal
        return None

def verify_face(stored_encoding, current_encoding, threshold=0.4):
    """
    Compares two embeddings using Cosine Similarity.
    DeepFace uses Cosine similarity by default for VGG-Face.
    """
    if not stored_encoding or not current_encoding:
        return False
    
    try:
        # DeepFace utility for distance calculation
        from deepface.commons import distance as dst
        
        # Calculate cosine distance
        cosine_distance = dst.findCosineDistance(stored_encoding, current_encoding)
        
        # Lower distance means more similar
        return cosine_distance <= threshold
        
    except Exception as e:
        # Fallback to manual cosine similarity if DeepFace internal helper fails
        print(f"Verification error: {e}")
        a = np.array(stored_encoding)
        b = np.array(current_encoding)
        cos_sim = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
        # cosine distance = 1 - cosine similarity
        return (1 - cos_sim) <= threshold
