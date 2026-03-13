from EmpManagement.models import emp_master

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
        result = DeepFace.represent(img_path=image, model_name=model_name, enforce_detection=False)
        
        if result and len(result) > 0:
            return result[0]["embedding"]
        return None
        
    except Exception as e:
        print(f"DeepFace encoding error: {str(e)}")
        return None

def verify_face(stored_encoding, current_encoding, threshold=0.4):
    """
    Compares two embeddings using Cosine Similarity.
    DeepFace uses Cosine similarity by default for VGG-Face.
    """
    if not stored_encoding or not current_encoding:
        return False
    
    try:
        a = np.array(stored_encoding)
        b = np.array(current_encoding)
        
        # Calculate Cosine Similarity
        # formula: (a . b) / (||a|| * ||b||)
        cos_sim = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
        
        # Convert to Cosine Distance (1 - Cosine Similarity)
        # Lower distance means more similar
        cosine_distance = 1 - cos_sim
        
        return cosine_distance <= threshold
        
    except Exception as e:
        print(f"Verification error: {e}")
        return False


def find_matching_employee(current_encoding, threshold=0.35):

    employees = emp_master.objects.exclude(face_encoding__isnull=True)

    if not employees.exists():
        return None

    current_vector = np.array(current_encoding)

    best_match = None
    best_distance = 1.0

    for emp in employees:

        stored_vector = np.array(emp.face_encoding)

        cos_sim = np.dot(current_vector, stored_vector) / (
            np.linalg.norm(current_vector) * np.linalg.norm(stored_vector)
        )

        cosine_distance = 1 - cos_sim

        if cosine_distance < best_distance:
            best_distance = cosine_distance
            best_match = emp

    if best_distance <= threshold:
        return best_match

    return None