VORA quick run
--------------
1) Create env (Conda):
   conda env create -f environment.yml
   conda activate vora
2) Start backend API:
   ./run_dev.sh
3) Open frontend:
   app/frontend/client.html (uses http://localhost:8000 and ws://localhost:8000)
4) Make sure dependencies are up:
   - SearxNG at http://127.0.0.1:8080
   - Typhoon2-audio at http://127.0.0.1:8100 (from docker-compose in Typhoon2a_audio)
