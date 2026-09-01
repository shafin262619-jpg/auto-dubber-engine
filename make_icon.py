from PIL import Image, ImageDraw

# Create a high-res 512x512 icon with transparent background
img = Image.new('RGBA', (512, 512), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Smooth dark rounded card background with a subtle border
draw.rounded_rectangle([16, 16, 496, 496], radius=110, fill="#0f172a", outline="#38bdf8", width=8)

# Draw a stylized modern AI/Cloud core motif (Geometrical layers)
draw.ellipse([130, 130, 382, 382], fill="#1e293b", outline="#6366f1", width=10)
draw.ellipse([180, 180, 332, 332], fill="#4f46e5")
draw.ellipse([220, 220, 292, 292], fill="#38bdf8")

# Save over the previous icon
icon_path = "/home/shafin/Desktop/BlueprintTube_Project/Claude-workstation/workspace/claude_icon.png"
img.save(icon_path)
print(f"Updated icon created at: {icon_path}")
