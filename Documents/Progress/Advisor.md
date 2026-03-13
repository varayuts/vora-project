VORA Development Tasks and Research Questions Based on Advisor Feedback

Navigation System Setup

Task
Enable and configure the Nav2 navigation stack for the robot so that it can perform reliable path planning and movement.

Key Questions
How should Nav2 be configured for the MyAGV robot?
What parameters affect navigation stability?
How should robot localization be verified?

Expected Output
Working Nav2 navigation stack
Robot able to move between waypoints
Verified localization accuracy

Environment Mapping

Task
Create a new SLAM map where the environment and obstacles are fixed.

Key Questions
How should the mapping process be standardized?
How can we ensure the map remains consistent for repeated experiments?

Expected Output
A stable SLAM map
Fixed environment for repeatable experiments

Path Following Experiment

Task
Test the robot’s ability to follow predefined waypoints.

Key Questions
Can the robot navigate to marked points on the map?
How accurate is waypoint navigation?
What is the navigation error?

Expected Output
Robot follows marked path on the map
Navigation accuracy evaluation

Experimental Design

Design experiments to evaluate the system in three areas.

4.1 STT Experiment

Task
Evaluate the Speech-to-Text performance.

Questions
How accurately does STT convert Thai voice commands?
What is the word error rate?

Expected Output
Metrics such as STT accuracy and Word Error Rate (WER).

4.2 VLM Experiment

Task
Evaluate the Vision-Language Model object recognition accuracy.

Questions
Can the VLM correctly identify objects?
Is the object detection accuracy close to 100%?

Expected Output
Metrics such as object recognition accuracy and detection reliability.

4.3 End-to-End System Experiment

Task
Evaluate the entire system pipeline.

System pipeline
Voice Command → STT → LLM Reasoning → VLM Perception → Nav2 Navigation → TTS Response

Questions
Does the full system work reliably?
What is the latency of the pipeline?
What is the task success rate?

Expected Output
Latency measurement
Task success rate
Pipeline reliability

Vision Processing Strategy

Task
Determine whether the robot should use image capture per motion or continuous video streaming.

Questions
Which method provides better perception accuracy?
Does streaming improve navigation smoothness?
Does image capture provide more stable inference?

Expected Output
Comparison between image capture and video streaming with reasoning and experimental justification.

LLM–Nav2 Communication

Task
Design a communication mechanism between LLM and Nav2.

Questions
How should LLM send navigation commands?
How should Nav2 report execution status?
How do we prevent command conflicts between planning and navigation?

Expected Output
Communication protocol such as
LLM generates navigation goal
Nav2 executes goal
Nav2 returns status
LLM decides the next action

System Pipeline Explanation

Task
Explain the interaction between system components including STT, LLM, VLM, Nav2, and TTS.

Questions
How does voice input propagate through the system?
How are commands translated into robot actions?

Expected Output
Pipeline explanation such as
User Voice → STT → LLM → VLM → Nav2 → TTS → User Response

Object Detection Edge Cases

Task
Evaluate how the system handles multiple objects in a scene.

Questions
Can the VLM detect multiple objects in the same image?
Can it distinguish overlapping or partially occluded objects?

Example scenario
Bottle next to a cup
Bottle partially covered by another object

Expected Output
Structured response such as
"I see a bottle next to a cup."

Multiple Target Scenario

Task
Determine system behavior when multiple target objects exist.

Example scenario
Bottle 1
Bottle 2
Bottle 3

Questions
Should the robot stop when it finds the first bottle?
Or should it continue searching and report all detected bottles?

Expected Output
Example response such as
"I found three bottles. One near the table. One under the chair. One beside the door."

Prompt context for Claude Opus

You are helping design experiments and system architecture for a robotics project called VORA (Voice Oriented Robotic Assistant). The tasks and research questions above were given by my advisor. Your job is to organize them into a clear experimental plan, suggest evaluation metrics, identify missing technical components, propose improvements to the architecture, and help convert them into slides for a research presentation.