from app import create_app
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = create_app()

if __name__ == '__main__':
    # Ensure screenshots dir exists if needed (though we're not using it in the new version currently)
    os.makedirs('screenshots', exist_ok=True)
    
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.getenv("PORT", 5000))
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
