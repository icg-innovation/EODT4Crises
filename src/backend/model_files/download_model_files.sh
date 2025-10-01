# Download the SAM and Spacenet model files

# SAM model (1.1GB)
curl -L -o sam_vit_b_01ec64.pth_test \
  https://huggingface.co/datasets/Gourieff/ReActor/resolve/main/models/sams/sam_vit_b_01ec64.pth

# Spacenet model (500MB)
curl -L -o spacenet_vitb_256_e10.ckpt_test \
  "https://huggingface.co/congrui/sam_road/resolve/main/spacenet_vitb_256_e10.ckpt?download=true"
