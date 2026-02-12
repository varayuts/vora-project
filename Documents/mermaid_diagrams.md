# VORA System Design - Mermaid Diagrams

> **⚠️ สำคัญ: วิธี Copy โค้ดที่ถูกต้อง**
> 
> เมื่อ copy โค้ดไปใช้ใน mermaid.live หรือ mermaidchart.com:
> - ✅ Copy **เฉพาะโค้ดข้างใน** (เริ่มจาก `graph TB` หรือ `sequenceDiagram`)
> - ❌ **อย่า** copy บรรทัด ` ```mermaid ` และ ` ``` `
> - ❌ **อย่า** copy มาพร้อม markdown code fence
>
> **ตัวอย่างที่ถูกต้อง:** เริ่มที่ `graph TB` จบที่ `fill:#f3e5f5`

---

## 🎯 1. System Overview - 3 Phase หลัก (แนะนำสำหรับนำเสนอ!)

### แบบง่าย - เข้าใจได้ภายใน 30 วินาที

**⚠️ Copy ตั้งแต่ `flowchart LR` ถึง `fill:#c8e6c9`**

```mermaid
flowchart LR
    subgraph Phase1["🎤 PHASE 1: INPUT<br/>รับเสียง → แปลงข้อความ"]
        User[👤 ผู้ใช้พูด<br/>วอร่า เดินหน้า]
        Mic[🎙️ Microphone<br/>Mobile/Web]
        STT[🤖 AI STT<br/>Faster-Whisper<br/>Thai Model]
        
        User --> Mic
        Mic -->|เสียง| STT
    end
    
    subgraph Phase2["🧠 PHASE 2: PROCESSING<br/>วิเคราะห์ → วางแผน"]
        Intent[⚡ Intent Parser<br/>Regex + LLM]
        Decision{ตัดสินใจ}
        Simple[คำสั่งง่าย<br/>Regex]
        Complex[คำสั่งซับซ้อน<br/>LLM AI]
        
        Intent --> Decision
        Decision -->|เดิน/หมุน/หยุด| Simple
        Decision -->|Multi-step| Complex
    end
    
    subgraph Phase3["🤖 PHASE 3: EXECUTION<br/>สั่งงาน → เคลื่อนที่"]
        Gateway[🚪 Gateway<br/>Command Executor]
        ROS[📡 ROSBridge]
        Robot[🦾 MyAGV Robot<br/>Motor Control]
        
        Gateway --> ROS
        ROS --> Robot
    end
    
    STT -->|ข้อความ| Intent
    Simple --> Gateway
    Complex --> Gateway
    Robot -.->|เสร็จสิ้น| User
    
    style Phase1 fill:#e3f2fd
    style Phase2 fill:#fff9c4
    style Phase3 fill:#c8e6c9
```

### แบบละเอียดขึ้นนิดนึง

```mermaid
graph TB
    Start([👤 User: วอร่า เดินหน้า 1 เมตร])
    
    subgraph Phase1["📥 PHASE 1: INPUT - Voice to Text"]
        direction TB
        A1[Audio Stream<br/>WebSocket]
        A2[Faster-Whisper STT<br/>Thai Model]
        A3[Text Output:<br/>วอร่า เดินหน้า 1 เมตร]
        
        A1 --> A2
        A2 --> A3
    end
    
    subgraph Phase2["⚙️ PHASE 2: PROCESSING - Understanding"]
        direction TB
        B1[Typo Correction<br/>วัวร่า → วอร่า]
        B2{Intent Classification}
        B3[Regex: เดิน/หมุน/หยุด]
        B4[LLM: Complex tasks]
        B5[Command Generation:<br/>move_forward, distance=1.0]
        
        B1 --> B2
        B2 -->|Simple| B3
        B2 -->|Complex| B4
        B3 --> B5
        B4 --> B5
    end
    
    subgraph Phase3["🎬 PHASE 3: EXECUTION - Robot Action"]
        direction TB
        C1[Physics Calculation<br/>velocity × time]
        C2[ROSBridge Command<br/>/cmd_vel topic]
        C3[Motor Controller<br/>Move 1 meter]
        C4[TTS Response:<br/>กำลังเดินหน้า]
        
        C1 --> C2
        C2 --> C3
        C3 --> C4
    end
    
    Start --> A1
    A3 --> B1
    B5 --> C1
    C4 --> End([✅ Complete])
    
    style Phase1 fill:#e1f5fe
    style Phase2 fill:#fff9c4
    style Phase3 fill:#c8e6c9
    style Start fill:#ffccbc
    style End fill:#a5d6a7
```

---

## 🔄 2. Workflow Diagram - แสดงการทำงานทีละขั้นตอน

**⚠️ แนะนำสำหรับอธิบายกระบวนการทำงาน**

```mermaid
sequenceDiagram
    autonumber
    
    participant User as 👤 User
    participant Phase1 as 🎤 PHASE 1<br/>STT
    participant Phase2 as 🧠 PHASE 2<br/>Intent Parser
    participant Phase3 as 🤖 PHASE 3<br/>Robot
    
    rect rgb(227, 242, 253)
        Note over User,Phase1: INPUT PHASE - รับเสียง
        User->>Phase1: พูดคำสั่ง: "วอร่า เดินหน้า"
        Phase1->>Phase1: แปลงเสียง → ข้อความ
        Phase1-->>User: แสดงข้อความ
    end
    
    rect rgb(255, 249, 196)
        Note over Phase1,Phase2: PROCESSING PHASE - วิเคราะห์
        Phase1->>Phase2: ส่งข้อความ
        Phase2->>Phase2: Regex: ตรวจสอบคำสั่ง
        
        alt คำสั่งง่าย
            Phase2->>Phase2: ใช้ Regex แยกพารามิเตอร์
        else คำสั่งซับซ้อน
            Phase2->>Phase2: ใช้ LLM วางแผน
        end
        
        Phase2->>Phase2: สร้างคำสั่งหุ่นยนต์
    end
    
    rect rgb(200, 230, 201)
        Note over Phase2,Phase3: EXECUTION PHASE - ทำงาน
        Phase2->>Phase3: ส่งคำสั่ง: move_forward(1m)
        Phase3->>Phase3: คำนวณ physics
        Phase3->>Phase3: สั่ง motor เคลื่อนที่
        Phase3-->>User: 🔊 "กำลังเดินหน้า"
        Phase3->>Phase3: เคลื่อนที่ 1 เมตร (10 วินาที)
        Phase3-->>User: ✅ "เสร็จสิ้น"
    end
```

---

## 📊 3. Phase Details - รายละเอียดแต่ละ Phase

### Phase 1: INPUT - Voice to Text (2-3 วินาที)

```mermaid
flowchart LR
    Input[🎤 User Voice<br/>วอร่า เดินหน้า]
    
    subgraph WebSocket[WebSocket Streaming]
        Chunk[Audio Chunks<br/>4KB/packet]
        Buffer[Audio Buffer]
    end
    
    subgraph STT[Faster-Whisper AI]
        VAD[Voice Detection<br/>หยุดพูด?]
        Model[Thai Model<br/>distil-large-v3]
        Output[Text Output]
    end
    
    Result[✅ Text:<br/>วอร่า เดินหน้า]
    
    Input --> Chunk
    Chunk --> Buffer
    Buffer --> VAD
    VAD -->|Yes| Model
    Model --> Output
    Output --> Result
    
    style Input fill:#ffccbc
    style Result fill:#c5e1a5
    style STT fill:#bbdefb
```

### Phase 2: PROCESSING - Understanding (1-2 วินาที)

```mermaid
flowchart TD
    Input[📝 Text Input<br/>วอร่า เดินหน้า]
    
    Fix[Typo Correction<br/>วัวร่า → วอร่า]
    
    Check{มีคำสั่ง<br/>control?}
    
    subgraph Fast[⚡ Fast Path - Regex]
        R1[Pattern Match<br/>เดิน|หมุน|เลี้ยว]
        R2[Extract Params<br/>distance, angle]
        R3[Generate Command<br/>instant]
    end
    
    subgraph Smart[🤖 Smart Path - LLM]
        L1[LLM Analysis<br/>Gemma3 AI]
        L2[Multi-step Plan<br/>Step 1, 2, 3...]
        L3[Generate Commands<br/>sequenced]
    end
    
    Output[📤 Robot Command<br/>JSON]
    
    Input --> Fix
    Fix --> Check
    Check -->|เดิน/หมุน| Fast
    Check -->|ซับซ้อน| Smart
    R1 --> R2 --> R3
    L1 --> L2 --> L3
    R3 --> Output
    L3 --> Output
    
    style Fast fill:#c8e6c9
    style Smart fill:#ffe0b2
```

### Phase 3: EXECUTION - Robot Action (5-15 วินาที)

```mermaid
flowchart LR
    Cmd[📥 Command<br/>move_forward<br/>distance: 1m]
    
    subgraph Gateway[Gateway Windows PC]
        Parse[Parse Command]
        Calc[Calculate Physics<br/>v=0.1 m/s<br/>t=10s]
    end
    
    subgraph ROS[ROSBridge Protocol]
        Topic[Publish /cmd_vel<br/>linear.x = 0.1]
    end
    
    subgraph Robot[MyAGV Jetson Nano]
        Motor[Motor Controller]
        Move[เคลื่อนที่]
        Sensor[Sensors Check]
    end
    
    Done[✅ Complete<br/>Report back]
    
    Cmd --> Parse
    Parse --> Calc
    Calc --> Topic
    Topic --> Motor
    Motor --> Move
    Move --> Sensor
    Sensor --> Done
    
    style Gateway fill:#fff3e0
    style ROS fill:#e1bee7
    style Robot fill:#b2dfdb
```

---

## 🎭 4. Innovation Highlight - Hybrid Intent Parser

**⚠️ ใช้อธิบาย Innovation ของโครงการ**

```mermaid
flowchart TD
    Start([User Input Text])
    
    Stage1{🔍 Stage 1<br/>Regex Check}
    
    subgraph Traditional[❌ Traditional Approach<br/>LLM Only - Slow 3-5s]
        T1[Send to LLM]
        T2[Wait for response]
        T3[Parse result]
    end
    
    subgraph Our[✅ Our Hybrid Approach<br/>Regex + LLM - Fast 0.5s]
        O1[Regex Pattern<br/>เดิน|หมุน|เลี้ยว]
        O2[Extract Params]
        O3[Instant Result]
    end
    
    LLM[🤖 LLM<br/>for complex only]
    
    Result[Command Output]
    
    Start --> Stage1
    Stage1 -->|80% Simple| Our
    Stage1 -->|20% Complex| LLM
    
    O1 --> O2 --> O3 --> Result
    LLM --> Result
    
    Traditional -.->|We don't use this| T1
    T1 -.-> T2 -.-> T3
    
    style Our fill:#c8e6c9
    style Traditional fill:#ffcdd2
    style LLM fill:#ffe0b2
```

---

## 🏗️ 5. System Architecture Overview (Original)

### เวอร์ชัน A: พร้อม Emoji (แนะนำ - สวยงาม)

**⚠️ Copy ตั้งแต่บรรทัด `graph TB` ถึง `fill:#f3e5f5` เท่านั้น**

```mermaid
graph TB
    subgraph User["👤 User Interface"]
        Mobile[📱 Mobile Device<br/>Microphone + Speaker]
        WebUI[🌐 Web Browser<br/>HTML/JavaScript]
    end

    subgraph Server["🖥️ AI Server - NVIDIA A6000"]
        WS[WebSocket Handler<br/>FastAPI]
        STT[🎤 Speech-to-Text<br/>Faster-Whisper<br/>Distil-Large-v3-TH]
        Intent[🧠 Intent Parser<br/>Regex + LLM Hybrid]
        LLM[🤖 Large Language Model<br/>Gemma3:27b-qat]
        TTS[🔊 Text-to-Speech<br/>Thai TTS]
        Session[💾 Session Manager<br/>Memory + Context]
    end

    subgraph Gateway["🚪 Gateway - Windows PC"]
        IntentExec[Intent Executor<br/>Command Parser]
        ROSClient[ROSBridge Client<br/>WebSocket]
        HealthCheck[Health Monitor]
    end

    subgraph Robot["🤖 MyAGV Robot - Jetson Nano"]
        ROSBridge[ROSBridge Server<br/>ws://192.168.0.111:9090]
        NavStack[ROS Navigation Stack<br/>SLAM + Path Planning]
        Motor[Motor Controller<br/>/cmd_vel Topic]
        Sensors[Sensors<br/>IMU + Odometry]
    end

    subgraph Network["🌐 Network Layer"]
        Tailscale[Tailscale VPN Mesh<br/>Encrypted Tunnel]
        HTTPS[HTTPS Certificate<br/>Auto-generated]
    end

    Mobile -->|Audio Stream| WS
    WebUI -->|WebSocket| WS
    WS -->|PCM Audio| STT
    STT -->|Text| Intent
    Intent -->|Simple| IntentExec
    Intent -->|Complex| LLM
    LLM -->|Multi-step Plan| IntentExec
    Intent -->|Response Text| TTS
    TTS -->|Audio| Mobile
    Session -.->|Context| Intent
    Session -.->|History| LLM

    IntentExec -->|Robot Command JSON| ROSClient
    ROSClient -->|WebSocket| ROSBridge
    ROSBridge -->|ROS Messages| NavStack
    NavStack -->|Velocity| Motor
    Motor -->|Motion| Sensors
    Sensors -.->|Feedback| NavStack
    HealthCheck -.->|Monitor| ROSClient
    HealthCheck -.->|Ping| Server

    Server -.->|Tailscale| Tailscale
    Gateway -.->|Tailscale| Tailscale
    Robot -.->|Local Network| Gateway

    style Server fill:#e1f5ff
    style Gateway fill:#fff4e1
    style Robot fill:#e8f5e9
    style Network fill:#f3e5f5
```

### เวอร์ชัน B: ไม่มี Emoji (สำหรับ parser ที่มีปัญหา)

**⚠️ หาก Mermaid ขึ้น error ให้ใช้เวอร์ชันนี้แทน**

```mermaid
graph TB
    subgraph User["User Interface"]
        Mobile[Mobile Device<br/>Microphone + Speaker]
        WebUI[Web Browser<br/>HTML/JavaScript]
    end

    subgraph Server["AI Server - NVIDIA A6000"]
        WS[WebSocket Handler<br/>FastAPI]
        STT[Speech-to-Text<br/>Faster-Whisper<br/>Distil-Large-v3-TH]
        Intent[Intent Parser<br/>Regex + LLM Hybrid]
        LLM[Large Language Model<br/>Gemma3:27b-qat]
        TTS[Text-to-Speech<br/>Thai TTS]
        Session[Session Manager<br/>Memory + Context]
    end

    subgraph Gateway["Gateway - Windows PC"]
        IntentExec[Intent Executor<br/>Command Parser]
        ROSClient[ROSBridge Client<br/>WebSocket]
        HealthCheck[Health Monitor]
    end

    subgraph Robot["MyAGV Robot - Jetson Nano"]
        ROSBridge[ROSBridge Server<br/>Port 9090]
        NavStack[ROS Navigation Stack<br/>SLAM + Path Planning]
        Motor[Motor Controller<br/>/cmd_vel Topic]
        Sensors[Sensors<br/>IMU + Odometry]
    end

    subgraph Network["Network Layer"]
        Tailscale[Tailscale VPN Mesh<br/>Encrypted Tunnel]
        HTTPS[HTTPS Certificate<br/>Auto-generated]
    end

    Mobile -->|Audio Stream| WS
    WebUI -->|WebSocket| WS
    WS -->|PCM Audio| STT
    STT -->|Text| Intent
    Intent -->|Simple Command| IntentExec
    Intent -->|Complex Command| LLM
    LLM -->|Multi-step Plan| IntentExec
    Intent -->|Response Text| TTS
    TTS -->|Audio| Mobile
    Session -.->|Context| Intent
    Session -.->|History| LLM

    IntentExec -->|Robot Command JSON| ROSClient
    ROSClient -->|WebSocket| ROSBridge
    ROSBridge -->|ROS Messages| NavStack
    NavStack -->|Velocity| Motor
    Motor -->|Motion| Sensors
    Sensors -.->|Feedback| NavStack
    HealthCheck -.->|Monitor| ROSClient
    HealthCheck -.->|Ping| Server

    Server -.->|Tailscale| Tailscale
    Gateway -.->|Tailscale| Tailscale
    Robot -.->|Local Network| Gateway

    style Server fill:#e1f5ff
    style Gateway fill:#fff4e1
    style Robot fill:#e8f5e9
    style Network fill:#f3e5f5
```

---

## 2. Detailed Workflow - Voice Command Processing

```mermaid
sequenceDiagram
    participant U as 👤 User
    participant M as 📱 Mobile/Web
    participant S as 🖥️ Server
    participant STT as 🎤 Faster-Whisper
    participant I as 🧠 Intent Parser
    participant L as 🤖 LLM
    participant G as 🚪 Gateway
    participant R as 🤖 Robot

    U->>M: Speaks command<br/>"วอร่า เดินหน้า 1 เมตร"
    M->>S: Audio stream (WebSocket)<br/>PCM 16kHz chunks
    
    Note over S: Audio buffering with VAD
    
    S->>STT: Process audio buffer
    activate STT
    STT->>STT: Transcribe (Thai model)<br/>+ VAD filtering
    STT-->>S: Text: "วอร่า เดินหน้า 1 เมตร"
    deactivate STT
    
    S->>S: Typo correction<br/>"วัวร่า" → "วอร่า"
    
    S->>I: Parse intent from text
    activate I
    
    alt Simple Command (Regex Match)
        I->>I: Regex: r"เดินหน้า"<br/>Extract: distance=1, unit="เมตร"
        I-->>S: Intent: "control"<br/>Action: "move_forward"<br/>Params: {distance: 1.0}
    else Complex Command
        I->>L: Query LLM for reasoning
        activate L
        L->>L: Analyze multi-step task<br/>Generate plan
        L-->>I: Steps: [{action: "move", ...}]
        deactivate L
        I-->>S: Intent: "control"<br/>Multi-step plan
    end
    deactivate I
    
    S->>S: Generate response text<br/>"กำลังเดินหน้า 1 เมตร"
    
    par Send TTS Response
        S->>M: TTS Audio + Text response
        M->>U: Play audio + Show text
    and Send Robot Command
        S->>G: Robot command JSON<br/>{action: "move_forward", distance: 1.0}
        activate G
        
        G->>G: Calculate physics<br/>velocity=0.1 m/s<br/>duration=10s
        
        G->>R: ROSBridge publish<br/>Topic: /cmd_vel<br/>linear.x=0.1, duration=10s
        activate R
        
        R->>R: Execute motion<br/>Move forward 1 meter
        
        R-->>G: Motion complete (feedback)
        deactivate R
        
        G-->>S: Execution status: "success"
        deactivate G
        
        S->>M: Status update: "เสร็จสิ้น"
        M->>U: Show completion
    end
```

---

## 3. Intent Classification Flow (Hybrid Approach)

```mermaid
flowchart TD
    Start([User Input Text]) --> Preprocess[Text Preprocessing<br/>- Remove punctuation<br/>- Lowercase<br/>- Typo correction]
    
    Preprocess --> Stage1{Stage 1: Regex<br/>Pre-filter}
    
    Stage1 -->|Match| RegexParse[Regex Pattern Match<br/>- หมุน/เลี้ยว/หัน → rotation<br/>- เดิน/ถอย → movement<br/>- หยุด → stop<br/>- หา/ไป → navigation]
    
    RegexParse --> ExtractParams[Extract Parameters<br/>- Angle: 90, 180, 360<br/>- Distance: 0.5, 1, 2<br/>- Direction: ซ้าย/ขวา<br/>- Unit: เมตร/cm/วินาที]
    
    ExtractParams --> SimpleIntent[Intent: control<br/>Action: immediate<br/>Confidence: HIGH]
    
    Stage1 -->|No Match| Stage2{Stage 2: Keyword<br/>Double-check}
    
    Stage2 -->|Has control keyword| Override[Force Override<br/>intent = control<br/>even if LLM says chitchat]
    
    Stage2 -->|No keyword| LLMQuery[Query LLM<br/>Gemma3:27b-it]
    
    LLMQuery --> LLMAnalyze{LLM Analysis}
    
    LLMAnalyze -->|chitchat| ChitChat[Intent: chitchat<br/>Generate friendly response]
    LLMAnalyze -->|question| Question[Intent: question<br/>Search knowledge base]
    LLMAnalyze -->|control| ComplexControl[Intent: control<br/>Multi-step planning]
    LLMAnalyze -->|navigation| Navigation[Intent: navigation<br/>Waypoint routing]
    
    Override --> SimpleIntent
    ComplexControl --> PlanSteps[Break into steps<br/>1. Move forward 1m<br/>2. Rotate 90°<br/>3. Move forward 0.5m]
    
    SimpleIntent --> Execute[Execute Command<br/>Send to Gateway]
    ChitChat --> Respond[Generate TTS Response]
    Question --> Respond
    PlanSteps --> Execute
    Navigation --> Execute
    
    Execute --> End([Command Sent])
    Respond --> End
    
    style Stage1 fill:#ffe0b2
    style Stage2 fill:#fff9c4
    style LLMQuery fill:#e1bee7
    style SimpleIntent fill:#c8e6c9
    style Execute fill:#b2dfdb
```

---

## 4. Robot Motion Control - Physics Calculation

```mermaid
flowchart LR
    subgraph Input[User Command]
        Cmd["เลี้ยวขวา 90 องศา"]
    end
    
    subgraph Parser[Command Parser]
        Extract[Extract Parameters<br/>- Action: rotate<br/>- Angle: 90<br/>- Direction: right ขวา]
    end
    
    subgraph Physics[Physics Calculation]
        Convert[Convert to radians<br/>90° = π/2 = 1.571 rad]
        Calibrate[Apply calibration<br/>FACTOR = 0.857<br/>adjusted = 1.571 * 0.857]
        Calc[Calculate duration<br/>duration = angle_rad / angular_vel<br/>= 1.346 / 0.3 rad/s<br/>= 4.49 seconds]
    end
    
    subgraph ROSCmd[ROS Command]
        Build[Build /cmd_vel message<br/>linear: {x:0, y:0, z:0}<br/>angular: {x:0, y:0, z:-0.3}]
        Publish[Publish for duration<br/>4.49 seconds]
    end
    
    subgraph Execution[Motor Execution]
        Motor[Motor Controller<br/>Spin right at 0.3 rad/s]
        Monitor[Monitor with IMU<br/>Track actual rotation]
        Stop[Stop motors<br/>after duration]
    end
    
    subgraph Feedback[Feedback Loop]
        Check{Rotation<br/>accurate?}
        Success[Report success]
        Adjust[Adjust calibration<br/>for next command]
    end
    
    Input --> Parser
    Parser --> Physics
    Convert --> Calibrate
    Calibrate --> Calc
    Physics --> ROSCmd
    Build --> Publish
    ROSCmd --> Execution
    Motor --> Monitor
    Monitor --> Stop
    Execution --> Feedback
    Check -->|Yes| Success
    Check -->|No| Adjust
    Adjust -.->|Update| Calibrate
    
    style Physics fill:#e3f2fd
    style Execution fill:#f3e5f5
    style Feedback fill:#fff3e0
```

---

## 5. STT Processing Pipeline - Latency Optimization

```mermaid
graph LR
    subgraph User[User Audio Input]
        Mic[🎤 Microphone<br/>ReSpeaker USB<br/>44.1kHz stereo]
    end
    
    subgraph Preprocessing[Audio Preprocessing]
        FFmpeg[FFmpeg Conversion<br/>→ 16kHz mono PCM<br/>→ 16-bit samples]
        Chunk[Chunk into 4KB blocks<br/>~0.1s per chunk]
    end
    
    subgraph Streaming[WebSocket Streaming]
        WS[WebSocket Connection<br/>wss://server/ws/stt]
        Buffer[Server-side Buffer<br/>Accumulate chunks]
        VAD[Voice Activity Detection<br/>Detect silence threshold]
    end
    
    subgraph STT[Faster-Whisper Engine]
        Load[Load Model<br/>distil-whisper-th-large-v3-ct2<br/>CUDA FP16]
        Transcribe[Transcribe with VAD<br/>- threshold: 0.5<br/>- min_speech: 250ms]
        Filter[Filter hallucinations<br/>Remove repeated segments]
    end
    
    subgraph Output[Text Output]
        Text[Transcribed Text<br/>"วอร่า เดินหน้า"]
        Latency[⏱️ Total Latency<br/>2-3 seconds]
    end
    
    Mic --> FFmpeg
    FFmpeg --> Chunk
    Chunk --> WS
    WS --> Buffer
    Buffer --> VAD
    VAD -->|Silence detected| Transcribe
    Load -.->|Model ready| Transcribe
    Transcribe --> Filter
    Filter --> Text
    Text --> Latency
    
    style Streaming fill:#e8f5e9
    style STT fill:#e1f5fe
    style Latency fill:#ffeb3b
```

---

## 6. Network Topology - Tailscale VPN Mesh

```mermaid
graph TB
    subgraph Internet[☁️ Internet]
        TS[Tailscale Coordination Server<br/>controlplane.tailscale.com]
    end
    
    subgraph VPN[🔒 Tailscale VPN Mesh Network]
        subgraph ServerNode[Server Node]
            ServerIP[user.tail87d9fe.ts.net<br/>100.x.x.1<br/>NVIDIA A6000]
            ServerServices[Services:<br/>- FastAPI :8000<br/>- Ollama :11434<br/>- WebSocket :8000/ws]
        end
        
        subgraph GatewayNode[Gateway Node]
            GatewayIP[gateway-pc.tail87d9fe.ts.net<br/>100.x.x.2<br/>Windows 11]
            GatewayServices[Services:<br/>- Gateway App :5001<br/>- ROSBridge Client]
        end
        
        subgraph LocalNet[🏠 Local Network 192.168.0.x]
            Router[WiFi Router<br/>192.168.0.1]
            
            subgraph RobotNode[Robot Node]
                RobotIP[myagv-jetson<br/>192.168.0.111<br/>Jetson Nano]
                RobotServices[Services:<br/>- ROSBridge :9090<br/>- ROS Master :11311<br/>- SLAM Mapping]
            end
        end
    end
    
    subgraph Client[👥 Client Devices]
        Mobile[📱 Mobile<br/>Any device<br/>Web browser]
    end
    
    TS -.->|Encrypted tunnel| ServerIP
    TS -.->|Encrypted tunnel| GatewayIP
    
    Mobile -->|HTTPS<br/>user.tail87d9fe.ts.net:8000| ServerServices
    ServerIP <-->|Commands| GatewayIP
    GatewayIP <-->|Local network| Router
    Router <-->|WiFi| RobotIP
    GatewayServices <-->|ws://192.168.0.111:9090| RobotServices
    
    style VPN fill:#e8eaf6
    style LocalNet fill:#f3e5f5
    style ServerNode fill:#e1f5fe
    style GatewayNode fill:#fff9c4
    style RobotNode fill:#c8e6c9
```

---

## 7. Multi-Step Command Execution

```mermaid
stateDiagram-v2
    [*] --> ReceiveCommand: User: "เดินหน้าแล้วเลี้ยวขวา"
    
    ReceiveCommand --> ParseIntent: STT Complete
    
    ParseIntent --> DetectMultiStep: Intent Parser
    
    DetectMultiStep --> LLMPlanning: Complex command detected
    
    LLMPlanning --> GenerateSteps: LLM breaks into steps
    
    GenerateSteps --> QueueSteps: Queue: [Step1, Step2]
    
    QueueSteps --> ExecuteStep1: Execute Step 1
    
    state ExecuteStep1 {
        [*] --> Move: "เดินหน้า"
        Move --> CalculatePhysics: distance = 1m (default)
        CalculatePhysics --> SendROS: linear.x = 0.1, t = 10s
        SendROS --> WaitComplete: Motor running
        WaitComplete --> [*]: Motion done
    }
    
    ExecuteStep1 --> CheckQueue: Step 1 complete
    
    CheckQueue --> ExecuteStep2: More steps remain
    
    state ExecuteStep2 {
        [*] --> Rotate: "เลี้ยวขวา"
        Rotate --> CalculateRotation: angle = 90° (default)
        CalculateRotation --> SendROSRotate: angular.z = -0.3, t = 4.5s
        SendROSRotate --> WaitRotate: Motor spinning
        WaitRotate --> [*]: Rotation done
    }
    
    ExecuteStep2 --> CheckQueue2: Step 2 complete
    
    CheckQueue2 --> AllComplete: Queue empty
    
    AllComplete --> SendFeedback: TTS: "เสร็จสิ้นครับ"
    
    SendFeedback --> [*]
    
    CheckQueue --> [*]: Error occurred
```

---

## 8. Error Handling & Recovery Flow

```mermaid
flowchart TD
    Start([Command Received]) --> Validate{Validate<br/>Command}
    
    Validate -->|Valid| Process[Process Normally]
    Validate -->|Invalid| Error1[Error: Invalid Input]
    
    Process --> Execute[Execute on Robot]
    
    Execute --> Timeout{Timeout<br/>Check}
    
    Timeout -->|Success within 30s| Success[Command Success]
    Timeout -->|Timeout| Error2[Error: Execution Timeout]
    
    Execute --> ROS{ROSBridge<br/>Connected?}
    
    ROS -->|Connected| Continue[Continue Execution]
    ROS -->|Disconnected| Error3[Error: Connection Lost]
    
    Error1 --> Notify[Notify User via TTS]
    Error2 --> Notify
    Error3 --> Notify
    
    Notify --> Retry{Retry<br/>Possible?}
    
    Retry -->|Yes| Reconnect[Attempt Reconnection]
    Retry -->|No| Log[Log Error]
    
    Reconnect --> CheckConn{Connection<br/>Restored?}
    
    CheckConn -->|Yes| Process
    CheckConn -->|No| Log
    
    Log --> Alert[Alert Administrator]
    Alert --> End([End])
    
    Continue --> Success
    Success --> End
    
    style Error1 fill:#ffcdd2
    style Error2 fill:#ffcdd2
    style Error3 fill:#ffcdd2
    style Success fill:#c8e6c9
```

---

## 9. System Components & Technologies

```mermaid
mindmap
    root((VORA System))
        Speech Processing
            STT Engine
                Faster-Whisper
                Distil-Large-v3-TH
                CTranslate2
                CUDA Acceleration
            TTS Engine
                gTTS
                Piper TTS planned
                Thai language support
            Audio Pipeline
                FFmpeg conversion
                WebSocket streaming
                VAD filtering
        AI & Intelligence
            LLM
                Gemma3:27b-qat
                Ollama framework
                Local inference
            Intent Parser
                Regex pre-filter
                LLM fallback
                Hybrid approach
            Memory System
                Session management
                Context tracking
                Conversation history
        Robot Control
            Hardware
                Elephant myAGV 2023
                Jetson Nano
                IMU sensors
            Software
                ROS Noetic
                ROSBridge
                Navigation Stack
            Motion
                /cmd_vel control
                SLAM mapping
                Waypoint navigation
        Infrastructure
            Backend
                FastAPI
                Python 3.10
                Async/await
            Networking
                Tailscale VPN
                HTTPS auto-cert
                WebSocket
            Deployment
                NVIDIA A6000 GPU
                Windows Gateway
                Ubuntu 20.04 Robot
```

---

## 10. Data Flow Architecture

```mermaid
flowchart TB
    subgraph Layer1[Presentation Layer]
        UI1[Web Interface<br/>HTML/CSS/JS]
        UI2[Mobile Browser<br/>PWA capable]
    end
    
    subgraph Layer2[API Layer]
        REST[REST API<br/>FastAPI endpoints]
        WS[WebSocket<br/>Bi-directional stream]
    end
    
    subgraph Layer3[Business Logic Layer]
        Auth[Authentication<br/>Session token]
        Pipeline[Processing Pipeline<br/>STT → Intent → TTS]
        Queue[Command Queue<br/>Multi-step executor]
    end
    
    subgraph Layer4[AI/ML Layer]
        Model1[Whisper Model<br/>1.5GB VRAM]
        Model2[Gemma3 Model<br/>16GB VRAM]
        Cache[Model Cache<br/>Response memoization]
    end
    
    subgraph Layer5[Integration Layer]
        Gateway[Gateway Service<br/>Command translator]
        ROSIntegration[ROS Integration<br/>ROSBridge protocol]
    end
    
    subgraph Layer6[Hardware Layer]
        Robot[MyAGV Robot<br/>Physical actuators]
        Sensors[Robot Sensors<br/>IMU, Odometry]
    end
    
    subgraph Storage[Data Storage]
        Session[Session DB<br/>In-memory]
        Logs[Log Files<br/>Rotating logs]
        Metrics[Metrics<br/>Prometheus format]
    end
    
    UI1 --> REST
    UI2 --> WS
    REST --> Auth
    WS --> Pipeline
    Auth --> Pipeline
    Pipeline --> Queue
    Pipeline --> Model1
    Pipeline --> Model2
    Model1 -.-> Cache
    Model2 -.-> Cache
    Queue --> Gateway
    Gateway --> ROSIntegration
    ROSIntegration --> Robot
    Robot --> Sensors
    Sensors -.->|Feedback| ROSIntegration
    
    Pipeline -.->|Save| Session
    Pipeline -.->|Write| Logs
    Gateway -.->|Metrics| Metrics
    
    style Layer1 fill:#e3f2fd
    style Layer2 fill:#f3e5f5
    style Layer3 fill:#fff3e0
    style Layer4 fill:#e8f5e9
    style Layer5 fill:#fce4ec
    style Layer6 fill:#e0f2f1
    style Storage fill:#f5f5f5
```

---

## วิธีใช้งาน Diagrams เหล่านี้:

1. **Copy code** จาก diagram ที่ต้องการ
2. ไปที่ **[mermaid.live](https://mermaid.live)** หรือ **[mermaidchart.com](https://www.mermaidchart.com)**
3. **Paste** code ลงไป
4. **Export** เป็น PNG/SVG/PDF สำหรับนำเสนอ

### Diagram แนะนำสำหรับการนำเสนอ:

- **Diagram 1**: System Architecture - ภาพรวมทั้งระบบ
- **Diagram 2**: Workflow Sequence - กระบวนการทำงานแบบละเอียด
- **Diagram 3**: Intent Classification - อธิบาย Hybrid approach ที่เป็น innovation
- **Diagram 6**: Network Topology - แสดงการ deployment จริง
- **Diagram 7**: Multi-Step Execution - แสดงความสามารถ advanced feature

### สีที่ใช้:
- 🔵 น้ำเงิน: AI/ML components
- 🟡 เหลือง: Gateway/Middleware
- 🟢 เขียว: Robot/Hardware
- 🟣 ม่วง: Network/Infrastructure
