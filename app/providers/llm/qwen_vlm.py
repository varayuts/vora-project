"""
Qwen2.5-VL-7B Integration for VORA
===================================
Vision Language Model for Thai object detection + understanding

Replaces separate vision + LLM pipeline with unified VLM
- Supports Thai language natively
- Faster inference (7B vs 27B main LLM)
- Native image understanding
"""

import torch
import asyncio
from typing import Optional, Dict, List
from pathlib import Path
from PIL import Image
import logging

logger = logging.getLogger(__name__)

# Lazy imports (load only when needed)
_vlm_model = None
_vlm_processor = None


def get_vlm_model():
    """Lazy load Qwen2.5-VL-7B-Instruct"""
    global _vlm_model, _vlm_processor
    
    if _vlm_model is not None:
        return _vlm_model, _vlm_processor
    
    logger.info("🚀 Loading Qwen2.5-VL-7B-Instruct...")
    from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
    
    model_name = "Qwen/Qwen2.5-VL-7B-Instruct"
    
    # Load with quantization for A6000 (48GB VRAM)
    _vlm_model = Qwen2VLForConditionalGeneration.from_pretrained(
        model_name,
        torch_dtype=torch.float16,  # FP16 for efficiency
        device_map="auto",  # Automatic device placement
        attn_implementation="flash_attention_2"  # Optional: faster attention
    )
    
    _vlm_processor = AutoProcessor.from_pretrained(model_name)
    
    logger.info("✅ Qwen2.5-VL-7B-Instruct loaded successfully")
    return _vlm_model, _vlm_processor


async def understand_image(
    image_path: str,
    prompt: str,
    lang: str = "th"
) -> Dict[str, any]:
    """
    Understand image content using VLM
    
    Args:
        image_path: Path to image file
        prompt: What to ask about the image (Thai or English)
        lang: Language ('th' or 'en')
    
    Returns:
        {
            "text": "ตอบ (Thai by default)",
            "confidence": 0.85,
            "objects": ["ไขควง", "ประแจ", ...],
            "description": "มีไขควง 1 อัน..."
        }
    """
    
    try:
        model, processor = get_vlm_model()
        
        # Load image
        if isinstance(image_path, str):
            image = Image.open(image_path).convert("RGB")
        else:
            image = image_path
        
        # Prepare prompt
        if "?" not in prompt:
            prompt = prompt + "?"
        
        # Add language hint
        if lang == "th":
            prompt = f"[Thai] {prompt}"
        
        logger.info(f"📸 Processing image with prompt: {prompt}")
        
        # Process inputs
        inputs = processor(
            text=prompt,
            images=[image],
            padding=True,
            return_tensors="pt"
        )
        
        # Move to GPU
        inputs = {k: v.to(model.device) if isinstance(v, torch.Tensor) else v 
                  for k, v in inputs.items()}
        
        # Generate response
        with torch.inference_mode():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=256,
                temperature=0.7,
                top_p=0.9,
            )
        
        # Decode response
        response = processor.batch_decode(
            output_ids,
            skip_special_tokens=True
        )[0]
        
        logger.info(f"✅ VLM Response: {response[:100]}...")
        
        return {
            "text": response,
            "confidence": 0.85,  # TODO: implement confidence scoring
            "model": "Qwen2.5-VL-7B",
            "lang": lang
        }
        
    except Exception as e:
        logger.error(f"❌ VLM Error: {e}")
        return {
            "text": "",
            "error": str(e),
            "confidence": 0.0
        }


async def find_object(
    image_path: str,
    object_name: str,
    lang: str = "th"
) -> Dict[str, any]:
    """
    Find specific object in image
    
    Example:
        find_object("room.jpg", "ไขควง", lang="th")
        → {"found": True, "location": "บนโต๊ะ", "count": 1}
    """
    
    if lang == "th":
        prompt = f"ในภาพนี้มี {object_name} หรือไม่? ถ้ามีอยู่ที่ไหน"
    else:
        prompt = f"Is there a {object_name} in this image? If yes, where?"
    
    result = await understand_image(image_path, prompt, lang)
    
    # Parse response to extract presence
    response_lower = result.get("text", "").lower()
    found = any(word in response_lower for word in ["มี", "ได้", "yes", "there"])
    
    return {
        **result,
        "object": object_name,
        "found": found,
        "description": result.get("text", "")
    }


async def describe_scene(
    image_path: str,
    lang: str = "th"
) -> Dict[str, any]:
    """
    Generate complete scene description
    """
    
    if lang == "th":
        prompt = "อธิบายว่าในภาพนี้มีสิ่งของอะไรบ้าง และจัดวางอยู่ยังไง"
    else:
        prompt = "Describe what objects are in this image and how they are arranged"
    
    return await understand_image(image_path, prompt, lang)


# Test function
if __name__ == "__main__":
    import asyncio
    
    async def test():
        # Create dummy image for testing
        from PIL import Image
        import tempfile
        
        # Create 512x512 dummy image
        img = Image.new('RGB', (512, 512), color='red')
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            img.save(tmp.name)
            
            # Test find_object
            result = await find_object(
                tmp.name,
                "สีแดง",
                lang="th"
            )
            print("Find Object Result:")
            print(result)
            
            # Test describe_scene
            result = await describe_scene(tmp.name, lang="th")
            print("\nDescribe Scene Result:")
            print(result)
    
    asyncio.run(test())
