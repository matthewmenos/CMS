"""
Image optimization utilities using Pillow.
Generates thumbnails and optimized versions of uploaded images.
"""

import os
from PIL import Image
from io import BytesIO

# Thumbnail sizes
THUMB_SIZES = {
    "avatar": (84, 84),
    "thumb": (150, 150),
    "small": (300, 300),
    "medium": (600, 600),
}


def generate_thumbnails(image_path: str, output_dir: str = None) -> dict:
    """
    Generate thumbnails for an image.
    
    Args:
        image_path: Path to the source image
        output_dir: Directory to save thumbnails (defaults to same dir as source)
    
    Returns:
        Dict mapping size names to output paths
    """
    if output_dir is None:
        output_dir = os.path.dirname(image_path)
    
    os.makedirs(output_dir, exist_ok=True)
    
    results = {}
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if necessary (for PNG with transparency)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            
            for size_name, (width, height) in THUMB_SIZES.items():
                # Create thumbnail maintaining aspect ratio
                thumb = img.copy()
                thumb.thumbnail((width, height), Image.Resampling.LANCZOS)
                
                # Save optimized
                output_path = os.path.join(output_dir, f"{base_name}_{size_name}.jpg")
                thumb.save(output_path, "JPEG", quality=85, optimize=True)
                results[size_name] = output_path
    
    except Exception as e:
        # Log error but don't crash
        import logging
        logging.getLogger(__name__).warning("Could not generate thumbnails: %s", e)
    
    return results


def optimize_image(image_path: str, quality: int = 85) -> int:
    """
    Optimize an image in place.
    
    Args:
        image_path: Path to the image to optimize
        quality: JPEG quality (1-100)
    
    Returns:
        Size saved in bytes
    """
    try:
        original_size = os.path.getsize(image_path)
        
        with Image.open(image_path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            
            # Save optimized version
            img.save(image_path, "JPEG", quality=quality, optimize=True)
        
        return original_size - os.path.getsize(image_path)
    
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Could not optimize image: %s", e)
        return 0


def get_image_dimensions(image_path: str) -> tuple:
    """
    Get image dimensions.
    
    Returns:
        Tuple of (width, height) or (0, 0) on error
    """
    try:
        with Image.open(image_path) as img:
            return img.size
    except Exception:
        return (0, 0)