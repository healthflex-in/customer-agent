// /**
//  * Client-side implementation for WebSocket connection to the Medical Interview API
//  * with automatic voice activation/deactivation and improved speech detection
//  */

// class MedicalInterviewClient {
//     constructor(serverUrl = 'ws://localhost:8000') {
//       this.serverUrl = serverUrl;
//       this.clientId = this.generateClientId();
//       this.websocket = null;
//       this.isConnected = false;
//       this.isRecording = false;
//       this.isModelSpeaking = false;
//       this.mediaRecorder = null;
//       this.audioChunks = [];
//       this.onMessageCallback = null;
//       this.onProgressCallback = null;
//       this.onErrorCallback = null;
//       this.onStatusChangeCallback = null;
//       this.onAudioLevelCallback = null;
      
//       // Audio processing parameters
//       this.silenceDetectionTimer = null;
//       this.silenceThreshold = 1500; // 1.5 seconds of silence before sending
//       this.silenceStartTime = null;
//       this.audioContext = null;
//       this.audioStream = null;
//       this.audioAnalyser = null;
//       this.speechDetected = false;
//       this.speakingThreshold = -65; // dB threshold for speech detection
//       this.consecutiveSilenceFrames = 0;
//       this.consecutiveSpeechFrames = 0;
//       this.requiredSilenceFrames = 30; // About 1.5 seconds (at 20ms intervals)
//       this.requiredSpeechFrames = 3; // About 60ms of speech to trigger speech detection
      
//       // Audio visualization
//       this.audioLevel = 0;
      
//       // Debug mode
//       this.debugMode = false;
//     }
  
//     /**
//      * Generate a unique client ID
//      */
//     generateClientId() {
//       return 'client_' + Math.random().toString(36).substring(2, 15);
//     }
  
//     /**
//      * Initialize the client with callbacks
//      */
//     init({ 
//       onMessage = (message) => console.log('Message:', message),
//       onProgress = (progress) => console.log('Progress:', progress),
//       onError = (error) => console.error('Error:', error),
//       onStatusChange = (status) => console.log('Status:', status),
//       onAudioLevel = (level) => {},
//       debugMode = false
//     } = {}) {
//       this.onMessageCallback = onMessage;
//       this.onProgressCallback = onProgress;
//       this.onErrorCallback = onError;
//       this.onStatusChangeCallback = onStatusChange;
//       this.onAudioLevelCallback = onAudioLevel;
//       this.debugMode = debugMode;
//     }
    
//     /**
//      * Log debug messages
//      */
//     log(...args) {
//       if (this.debugMode) {
//         console.log(...args);
//       }
//     }
  
//     /**
//      * Connect to the WebSocket server
//      */
//     async connect() {
//       try {
//         this.log('Connecting to server...');
        
//         // First create the interview session via REST API
//         const response = await fetch(`${this.serverUrl.replace('wss://', 'https://').replace('ws://', 'http:')}/start_interview`, {
//           method: 'POST',
//           headers: { 'Content-Type': 'application/json' },
//           body: JSON.stringify({ client_id: this.clientId })
//         });
        
//         if (!response.ok) {
//           throw new Error(`Failed to create interview session: ${response.statusText}`);
//         }
        
//         this.log('Interview session created, establishing WebSocket connection...');
        
//         // Then establish WebSocket connection
//         this.websocket = new WebSocket(`${this.serverUrl}/ws/${this.clientId}`);
        
//         this.websocket.onopen = () => {
//           this.isConnected = true;
//           this.updateStatus('connected');
//           this.log('WebSocket connection established');
//         };
        
//         this.websocket.onmessage = async (event) => {
//           try {
//             const data = JSON.parse(event.data);
            
//             if (data.type === 'text') {
//               // Model is speaking - pause recording
//               this.isModelSpeaking = true;
//               this.updateStatus('model-speaking');
              
//               if (this.isRecording) {
//                 await this.pauseRecording();
//               }
              
//               // Handle text message from server
//               this.onMessageCallback(data.message);
              
//               // Play message using speech synthesis
//               if ('speechSynthesis' in window) {
//                 this.speakMessage(data.message);
//               }
              
//               // After a delay to let any text-to-speech finish, resume recording
//               const wordCount = data.message.split(' ').length;
//               const delayTime = Math.max(500, wordCount * 90); // At least 500ms, 90ms per word
              
//               this.log(`Setting delay of ${delayTime}ms before resuming recording`);
              
//               setTimeout(() => {
//                 this.isModelSpeaking = false;
//                 this.updateStatus('ready-to-record');
                
//                 // Auto-resume recording
//                 if (this.isConnected && !this.isRecording) {
//                   this.startRecording();
//                 }
//               }, delayTime);
              
//             } else if (data.type === 'progress') {
//               // Handle progress update from server
//               this.onProgressCallback({
//                 progress: data.progress,
//                 currentSection: data.current_section
//               });
//             }
//           } catch (error) {
//             this.log('Error processing message:', error);
//             this.onErrorCallback(error);
//           }
//         };
        
//         this.websocket.onerror = (error) => {
//           this.log('WebSocket error:', error);
//           this.onErrorCallback(error);
//         };
        
//         this.websocket.onclose = () => {
//           this.isConnected = false;
//           this.updateStatus('disconnected');
          
//           // Clean up recording if active
//           if (this.isRecording) {
//             this.stopRecording();
//           }
          
//           this.log('WebSocket connection closed');
//         };
        
//         // After connecting, automatically start recording after a short delay
//         setTimeout(() => {
//           if (this.isConnected && !this.isModelSpeaking) {
//             this.startRecording();
//           }
//         }, 1000);
        
//         return true;
//       } catch (error) {
//         this.log('Connection error:', error);
//         this.onErrorCallback(error);
//         return false;
//       }
//     }
    
//     /**
//      * Speak a message using the Web Speech API
//      */
//     speakMessage(message) {
//       if (!('speechSynthesis' in window)) {
//         this.log('Speech synthesis not supported');
//         return;
//       }
      
//       // Cancel any ongoing speech
//       window.speechSynthesis.cancel();
      
//       // Create a new speech synthesis utterance
//       const utterance = new SpeechSynthesisUtterance(message);
      
//       // Get available voices and select a good one if possible
//       const voices = window.speechSynthesis.getVoices();
//       if (voices.length > 0) {
//         // Try to find a good voice - prefer female voices as they tend to be clearer
//         const preferredVoices = voices.filter(voice => 
//           voice.name.includes('Female') || 
//           voice.name.includes('Google') ||
//           voice.name.includes('en-US')
//         );
        
//         if (preferredVoices.length > 0) {
//           utterance.voice = preferredVoices[0];
//         }
//       }
      
//       // Set other properties
//       utterance.pitch = 1;
//       utterance.rate = 1;
//       utterance.volume = 1;
      
//       // Speak the message
//       window.speechSynthesis.speak(utterance);
//     }
  
//     /**
//      * Update status and notify callback
//      */
//     updateStatus(status) {
//       if (this.onStatusChangeCallback) {
//         this.onStatusChangeCallback(status);
//       }
//     }
  
//     /**
//      * Disconnect from the WebSocket server
//      */
//     disconnect() {
//       if (this.websocket && this.isConnected) {
//         this.sendCommand('exit');
//         this.websocket.close();
//         this.isConnected = false;
//       }
      
//       if (this.isRecording) {
//         this.stopRecording();
//       }
      
//       // Clean up audio context
//       if (this.audioContext) {
//         this.audioContext.close();
//         this.audioContext = null;
//       }
      
//       // Cancel any speech synthesis
//       if ('speechSynthesis' in window) {
//         window.speechSynthesis.cancel();
//       }
//     }
  
//     /**
//      * Start audio context for speech detection
//      */
//     async initAudioContext() {
//       if (!this.audioContext) {
//         try {
//           // Get user media stream
//           this.audioStream = await navigator.mediaDevices.getUserMedia({ 
//             audio: { 
//               echoCancellation: true,
//               noiseSuppression: true,
//               autoGainControl: true
//             } 
//           });
          
//           // Create audio context
//           this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
//           const micSource = this.audioContext.createMediaStreamSource(this.audioStream);
          
//           // Create analyser for volume detection
//           this.audioAnalyser = this.audioContext.createAnalyser();
//           this.audioAnalyser.fftSize = 2048;
//           this.audioAnalyser.minDecibels = -90;
//           this.audioAnalyser.maxDecibels = -10;
//           this.audioAnalyser.smoothingTimeConstant = 0.85;
          
//           micSource.connect(this.audioAnalyser);
          
//           // Start monitoring for speech
//           this.monitorSpeech();
          
//           return true;
//         } catch (error) {
//           this.log('Error initializing audio context:', error);
//           this.onErrorCallback(error);
//           return false;
//         }
//       }
//       return true;
//     }
    
//     /**
//      * Monitor audio to detect speech and silence
//      */
//     monitorSpeech() {
//       if (!this.audioAnalyser) return;
      
//       const bufferLength = this.audioAnalyser.frequencyBinCount;
//       const dataArray = new Uint8Array(bufferLength);
      
//       const checkAudio = () => {
//         if (!this.audioAnalyser) {
//           return;
//         }
        
//         this.audioAnalyser.getByteFrequencyData(dataArray);
        
//         // Calculate volume level
//         let sum = 0;
//         for (let i = 0; i < bufferLength; i++) {
//           sum += dataArray[i];
//         }
        
//         const average = sum / bufferLength;
//         const volume = 20 * Math.log10(average / 255 || 0.01); // Convert to dB, avoid log(0)
        
//         // Update audio level for visualization
//         this.audioLevel = Math.max(0, 1 + (volume / 40)); // Convert to 0-1 scale
//         if (this.onAudioLevelCallback) {
//           this.onAudioLevelCallback(this.audioLevel);
//         }
        
//         // Only process if recording
//         if (this.isRecording) {
//           // Detect speech
//           const wasSpeaking = this.speechDetected;
          
//           // Hysteresis for speech detection to avoid rapid switching
//           if (volume > this.speakingThreshold) {
//             this.consecutiveSpeechFrames++;
//             this.consecutiveSilenceFrames = 0;
            
//             if (this.consecutiveSpeechFrames >= this.requiredSpeechFrames) {
//               this.speechDetected = true;
//             }
//           } else {
//             this.consecutiveSilenceFrames++;
//             this.consecutiveSpeechFrames = 0;
            
//             if (this.consecutiveSilenceFrames >= this.requiredSilenceFrames) {
//               this.speechDetected = false;
//             }
//           }
          
//           // If speech started, reset silence timer
//           if (this.speechDetected && !wasSpeaking) {
//             this.log('Speech detected');
//             this.silenceStartTime = null;
            
//             // If we were going to send audio due to silence, cancel that
//             if (this.silenceDetectionTimer) {
//               clearTimeout(this.silenceDetectionTimer);
//               this.silenceDetectionTimer = null;
//             }
//           }
          
//           // If speech ended, start silence timer
//           if (!this.speechDetected && wasSpeaking) {
//             this.log('Silence started');
//             this.silenceStartTime = Date.now();
//           }
          
//           // If in silence for a while, send the recorded audio
//           if (!this.speechDetected && this.silenceStartTime) {
//             const silenceDuration = Date.now() - this.silenceStartTime;
            
//             if (silenceDuration > this.silenceThreshold && !this.silenceDetectionTimer) {
//               this.log(`Silence threshold reached (${silenceDuration}ms)`);
              
//               // Set a timer to send the audio after a bit more silence to confirm
//               this.silenceDetectionTimer = setTimeout(() => {
//                 if (this.isRecording && !this.speechDetected) {
//                   this.log('Sending audio after silence detection');
//                   this.pauseRecording();
//                   this.sendAudio();
//                   this.silenceStartTime = null;
//                   this.silenceDetectionTimer = null;
//                 }
//               }, 300); // Small additional delay to confirm silence
//             }
//           }
//         }
        
//         // Continue monitoring if still connected
//         if (this.isConnected) {
//           requestAnimationFrame(checkAudio);
//         }
//       };
      
//       checkAudio();
//     }
  
//     /**
//      * Start recording audio
//      */
//     async startRecording() {
//       if (!this.isConnected) {
//         this.onErrorCallback(new Error('Not connected to server'));
//         return false;
//       }
      
//       if (this.isRecording) {
//         return true; // Already recording
//       }
      
//       try {
//         // Initialize audio context for speech detection
//         const audioContextInitialized = await this.initAudioContext();
//         if (!audioContextInitialized) {
//           throw new Error('Could not initialize audio context');
//         }
        
//         this.mediaRecorder = new MediaRecorder(this.audioStream, {
//           mimeType: 'audio/webm;codecs=opus'
//         });
        
//         this.audioChunks = [];
        
//         this.mediaRecorder.ondataavailable = (event) => {
//           if (event.data.size > 0) {
//             this.audioChunks.push(event.data);
//           }
//         };
        
//         // Start recording
//         this.mediaRecorder.start(100); // Collect data in small chunks
//         this.isRecording = true;
//         this.updateStatus('recording');
//         this.log('Recording started');
        
//         return true;
//       } catch (error) {
//         this.log('Error starting recording:', error);
//         this.onErrorCallback(error);
//         return false;
//       }
//     }
  
//     /**
//      * Pause recording without sending audio yet
//      */
//     async pauseRecording() {
//       if (this.mediaRecorder && this.isRecording && this.mediaRecorder.state === 'recording') {
//         // Stop the media recorder but keep the chunks
//         this.mediaRecorder.stop();
//         this.isRecording = false;
//         this.updateStatus('paused');
        
//         // Need to wait for the ondataavailable event to fire
//         return new Promise(resolve => {
//           setTimeout(() => {
//             this.log('Recording paused');
//             resolve();
//           }, 100);
//         });
//       }
//       return Promise.resolve();
//     }
  
//     /**
//      * Send the collected audio to the server
//      */
//     async sendAudio() {
//       if (this.audioChunks.length === 0) {
//         this.log('No audio to send');
//         return;
//       }
      
//       try {
//         this.updateStatus('processing');
        
//         // Create a blob from the audio chunks
//         const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm;codecs=opus' });
//         this.audioChunks = []; // Clear for next recording
        
//         // Convert blob to array buffer
//         const arrayBuffer = await audioBlob.arrayBuffer();
        
//         // Send the audio data if the connection is open
//         if (this.isConnected && this.websocket && this.websocket.readyState === WebSocket.OPEN) {
//           this.websocket.send(arrayBuffer);
//           this.log('Audio sent to server, size:', arrayBuffer.byteLength);
          
//           // Signal to the server that speech has ended
//           this.sendCommand('end_speech');
//         } else {
//           this.log('WebSocket not ready, cannot send audio');
//         }
//       } catch (error) {
//         this.log('Error sending audio:', error);
//         this.onErrorCallback(error);
//       }
//     }
  
//     /**
//      * Send a command to the server
//      */
//     sendCommand(command, data = {}) {
//       if (!this.isConnected || !this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
//         this.log('Cannot send command, not connected');
//         return false;
//       }
      
//       try {
//         const message = JSON.stringify({
//           type: 'command',
//           command: command,
//           ...data
//         });
        
//         this.websocket.send(message);
//         this.log('Command sent:', command);
//         return true;
//       } catch (error) {
//         this.log('Error sending command:', error);
//         this.onErrorCallback(error);
//         return false;
//       }
//     }
  
//     /**
//      * Stop recording and clean up
//      */
//     stopRecording() {
//       if (this.mediaRecorder && this.isRecording && this.mediaRecorder.state === 'recording') {
//         this.mediaRecorder.stop();
//         this.isRecording = false;
//         this.updateStatus('stopped');
//         this.log('Recording stopped');
//       }
      
//       // Clear any pending silence timers
//       if (this.silenceDetectionTimer) {
//         clearTimeout(this.silenceDetectionTimer);
//         this.silenceDetectionTimer = null;
//       }
//     }
  
//     /**
//      * Get the current interview status
//      */
//     async getInterviewStatus() {
//       try {
//         const response = await fetch(`${this.serverUrl.replace('wss://', 'https://').replace('ws://', 'http:')}/interview_status/${this.clientId}`);
//         if (!response.ok) {
//           throw new Error(`Failed to get interview status: ${response.statusText}`);
//         }
//         return await response.json();
//       } catch (error) {
//         this.log('Error getting interview status:', error);
//         this.onErrorCallback(error);
//         return null;
//       }
//     }
  
//     /**
//      * Get the current interview data
//      */
//     async getInterviewData() {
//       try {
//         const response = await fetch(`${this.serverUrl.replace('wss://', 'https://').replace('ws://', 'http:')}/interview_data/${this.clientId}`);
//         if (!response.ok) {
//           throw new Error(`Failed to get interview data: ${response.statusText}`);
//         }
//         return await response.json();
//       } catch (error) {
//         this.log('Error getting interview data:', error);
//         this.onErrorCallback(error);
//         return null;
//       }
//     }
  
//     /**
//      * Save the current interview data
//      */
//     async saveInterview() {
//       try {
//         const response = await fetch(`${this.serverUrl.replace('wss://', 'https://').replace('ws://', 'http:')}/save_interview/${this.clientId}`, {
//           method: 'POST'
//         });
//         if (!response.ok) {
//           throw new Error(`Failed to save interview: ${response.statusText}`);
//         }
//         return await response.json();
//       } catch (error) {
//         this.log('Error saving interview:', error);
//         this.onErrorCallback(error);
//         return null;
//       }
//     }
//   }

/**
 * Client-side implementation for WebSocket connection to the Medical Interview API
 * with automatic voice activation/deactivation and improved speech detection
 */

class MedicalInterviewClient {
    constructor(serverUrl = null, userId = null, centerId = null, formId = null) {
      // Use production backend URL by default, fallback to localhost for development
      if (!serverUrl) {
        // Detect if running in production (HTTPS) or development (localhost)
        const isProduction = typeof window !== 'undefined' && 
          (window.location.protocol === 'https:' || 
           window.location.hostname === 'customerai.stance.health');
        serverUrl = isProduction 
          ? 'wss://customeragent.stance.health'
          : 'ws://localhost:8000';
      }
      this.serverUrl = serverUrl;
      this.clientId = this.generateClientId();
      
      // Get userId, centerId and formId from parameters, URL, or localStorage
      if (typeof window !== 'undefined') {
        const urlParams = new URLSearchParams(window.location.search);

        // userId resolution: explicit param → URL → localStorage
        if (!userId) {
          userId =
            urlParams.get('userId') ||
            urlParams.get('user_id') ||
            localStorage.getItem('userId') ||
            localStorage.getItem('user_id');
        }

        // centerId resolution: explicit param → URL → localStorage
        if (!centerId) {
          centerId =
            urlParams.get('centerId') ||
            urlParams.get('center_id') ||
            localStorage.getItem('centerId') ||
            localStorage.getItem('center_id');
        }

        // formId resolution: explicit param → URL → localStorage
        if (!formId) {
          formId =
            urlParams.get('formId') ||
            urlParams.get('form_id') ||
            localStorage.getItem('formId') ||
            localStorage.getItem('form_id');
        }
      }

      this.userId = userId;
      this.centerId = centerId;
      this.formId = formId;
      
      this.websocket = null;
      this.isConnected = false;
      this.isRecording = false;
      this.isModelSpeaking = false;
      this.mediaRecorder = null;
      this.audioChunks = [];
      this.onMessageCallback = null;
      this.onProgressCallback = null;
      this.onErrorCallback = null;
      this.onStatusChangeCallback = null;
      this.onAudioLevelCallback = null;
      
      // Audio processing parameters
      this.silenceDetectionTimer = null;
      this.silenceThreshold = 1000; // 1 second of silence before sending (reduced from 1500ms)
      this.silenceStartTime = null;
      this.audioContext = null;
      this.audioStream = null;
      this.audioAnalyser = null;
      this.speechDetected = false;
      this.speakingThreshold = -60; // dB threshold for speech detection (increased from -65)
      this.consecutiveSilenceFrames = 0;
      this.consecutiveSpeechFrames = 0;
      this.requiredSilenceFrames = 20; // About 1 second (at 20ms intervals) - reduced from 30
      this.requiredSpeechFrames = 3; // About 60ms of speech to trigger speech detection
      
      // Audio visualization
      this.audioLevel = 0;
      
      // Debug mode
      this.debugMode = false;
      
      // Last sent audio size
      this.lastSentAudioSize = 0;
    }
  
    /**
     * Generate a unique client ID
     */
    generateClientId() {
      return 'client_' + Math.random().toString(36).substring(2, 15);
    }
  
    /**
     * Initialize the client with callbacks
     */
    init({ 
      onMessage = (message) => console.log('Message:', message),
      onProgress = (progress) => console.log('Progress:', progress),
      onError = (error) => console.error('Error:', error),
      onStatusChange = (status) => console.log('Status:', status),
      onAudioLevel = (level) => {},
      debugMode = false
    } = {}) {
      this.onMessageCallback = onMessage;
      this.onProgressCallback = onProgress;
      this.onErrorCallback = onError;
      this.onStatusChangeCallback = onStatusChange;
      this.onAudioLevelCallback = onAudioLevel;
      this.debugMode = debugMode;
    }
    
    /**
     * Log debug messages
     */
    log(...args) {
      if (this.debugMode) {
        console.log(...args);
      }
    }
  
    /**
     * Connect to the WebSocket server
     */
    async connect() {
      try {
        this.log('Connecting to server...');
        
        // First create the interview session via REST API
        // Convert WebSocket URL to HTTP/HTTPS URL
        const httpUrl = this.serverUrl.replace('wss://', 'https://').replace('ws://', 'http:');
        const headers = { 'Content-Type': 'application/json' };
        // Include user/center identifiers in headers so upstream services can see them
        if (this.userId) {
          headers['X-User-Id'] = this.userId;
        }
        if (this.centerId) {
          headers['X-Center-Id'] = this.centerId;
        }

        const response = await fetch(`${httpUrl}/start_interview`, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            client_id: this.clientId,
            userId: this.userId || null,
            centerId: this.centerId || null,
            formId: this.formId || null,
          }),
        });
        
        if (!response.ok) {
          throw new Error(`Failed to create interview session: ${response.statusText}`);
        }
        
        this.log('Interview session created, establishing WebSocket connection...');
        
        // Then establish WebSocket connection
        this.websocket = new WebSocket(`${this.serverUrl}/ws/${this.clientId}`);
        
        this.websocket.onopen = () => {
          this.isConnected = true;
          this.updateStatus('connected');
          this.log('WebSocket connection established');
          
          // CRITICAL: Send start_interview message with userId (and optional formId) immediately after connection
          // This ensures the backend knows which user/form is being started or resumed
          if (this.userId) {
            this.sendStartInterview(this.userId, this.formId || null);
          } else {
            this.log('WARNING: No userId available. Backend will require userId in start_interview message.');
            this.onErrorCallback(new Error('userId is required. Please provide userId when creating MedicalInterviewClient or set it in URL/localStorage.'));
          }
        };
        
        this.websocket.onmessage = async (event) => {
          try {
            const data = JSON.parse(event.data);
            
            if (data.type === 'text') {
              // Model is speaking - pause recording
              this.isModelSpeaking = true;
              this.updateStatus('model-speaking');
              
              if (this.isRecording) {
                await this.pauseRecording();
              }
              
              // Handle text message from server
              this.onMessageCallback(data.message);
              
              // Play message using speech synthesis
              if ('speechSynthesis' in window) {
                this.speakMessage(data.message);
              }
              
              // After a delay to let any text-to-speech finish, resume recording
              const wordCount = data.message.split(' ').length;
              const delayTime = Math.max(500, wordCount * 90); // At least 500ms, 90ms per word
              
              this.log(`Setting delay of ${delayTime}ms before resuming recording`);
              
              setTimeout(() => {
                this.isModelSpeaking = false;
                this.updateStatus('ready-to-record');
                
                // Auto-resume recording
                if (this.isConnected && !this.isRecording) {
                  this.startRecording();
                }
              }, delayTime);
              
            } else if (data.type === 'progress') {
              // Handle progress update from server
              this.onProgressCallback({
                progress: data.progress,
                currentSection: data.current_section
              });
            }
          } catch (error) {
            this.log('Error processing message:', error);
            this.onErrorCallback(error);
          }
        };
        
        this.websocket.onerror = (error) => {
          this.log('WebSocket error:', error);
          this.onErrorCallback(error);
        };
        
        this.websocket.onclose = () => {
          this.isConnected = false;
          this.updateStatus('disconnected');
          
          // Clean up recording if active
          if (this.isRecording) {
            this.stopRecording();
          }
          
          this.log('WebSocket connection closed');
        };
        
        // After connecting, automatically start recording after a short delay
        setTimeout(() => {
          if (this.isConnected && !this.isModelSpeaking) {
            this.startRecording();
          }
        }, 1000);
        
        return true;
      } catch (error) {
        this.log('Connection error:', error);
        this.onErrorCallback(error);
        return false;
      }
    }
    
    /**
     * Speak a message using the Web Speech API
     */
    speakMessage(message) {
      if (!('speechSynthesis' in window)) {
        this.log('Speech synthesis not supported');
        return;
      }
      
      // Cancel any ongoing speech
      window.speechSynthesis.cancel();
      
      // Create a new speech synthesis utterance
      const utterance = new SpeechSynthesisUtterance(message);
      
      // Get available voices and select a good one if possible
      const voices = window.speechSynthesis.getVoices();
      if (voices.length > 0) {
        // Try to find a good voice - prefer female voices as they tend to be clearer
        const preferredVoices = voices.filter(voice => 
          voice.name.includes('Female') || 
          voice.name.includes('Google') ||
          voice.name.includes('en-US')
        );
        
        if (preferredVoices.length > 0) {
          utterance.voice = preferredVoices[0];
        }
      }
      
      // Set other properties
      utterance.pitch = 1;
      utterance.rate = 1;
      utterance.volume = 1;
      
      // Speak the message
      window.speechSynthesis.speak(utterance);
    }
  
    /**
     * Update status and notify callback
     */
    updateStatus(status) {
      if (this.onStatusChangeCallback) {
        this.onStatusChangeCallback(status);
      }
    }
  
    /**
     * Disconnect from the WebSocket server
     */
    disconnect() {
      if (this.websocket && this.isConnected) {
        this.sendCommand('exit');
        this.websocket.close();
        this.isConnected = false;
      }
      
      if (this.isRecording) {
        this.stopRecording();
      }
      
      // Clean up audio context
      if (this.audioContext) {
        this.audioContext.close();
        this.audioContext = null;
      }
      
      // Cancel any speech synthesis
      if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
    }
  
    /**
     * Start audio context for speech detection
     */
    async initAudioContext() {
      if (!this.audioContext) {
        try {
          // Get user media stream
          this.audioStream = await navigator.mediaDevices.getUserMedia({ 
            audio: { 
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true
            } 
          });
          
          // Create audio context
          this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
          const micSource = this.audioContext.createMediaStreamSource(this.audioStream);
          
          // Create analyser for volume detection
          this.audioAnalyser = this.audioContext.createAnalyser();
          this.audioAnalyser.fftSize = 2048;
          this.audioAnalyser.minDecibels = -90;
          this.audioAnalyser.maxDecibels = -10;
          this.audioAnalyser.smoothingTimeConstant = 0.85;
          
          micSource.connect(this.audioAnalyser);
          
          // Start monitoring for speech
          this.monitorSpeech();
          
          return true;
        } catch (error) {
          this.log('Error initializing audio context:', error);
          this.onErrorCallback(error);
          return false;
        }
      }
      return true;
    }
    
    /**
     * Monitor audio to detect speech and silence
     */
    monitorSpeech() {
      if (!this.audioAnalyser) return;
      
      const bufferLength = this.audioAnalyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      
      const checkAudio = () => {
        if (!this.audioAnalyser) {
          return;
        }
        
        this.audioAnalyser.getByteFrequencyData(dataArray);
        
        // Calculate volume level
        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
          sum += dataArray[i];
        }
        
        const average = sum / bufferLength;
        const volume = 20 * Math.log10(average / 255 || 0.01); // Convert to dB, avoid log(0)
        
        // Update audio level for visualization
        this.audioLevel = Math.max(0, 1 + (volume / 40)); // Convert to 0-1 scale
        if (this.onAudioLevelCallback) {
          this.onAudioLevelCallback(this.audioLevel);
        }
        
        // Only process if recording
        if (this.isRecording) {
          // Detect speech
          const wasSpeaking = this.speechDetected;
          
          // Hysteresis for speech detection to avoid rapid switching
          if (volume > this.speakingThreshold) {
            this.consecutiveSpeechFrames++;
            this.consecutiveSilenceFrames = 0;
            
            if (this.consecutiveSpeechFrames >= this.requiredSpeechFrames) {
              this.speechDetected = true;
            }
          } else {
            this.consecutiveSilenceFrames++;
            
            if (this.consecutiveSilenceFrames >= this.requiredSilenceFrames) {
              // Only reset speech frames if we've had enough silence
              this.consecutiveSpeechFrames = 0;
              
              // If we were speaking and now have enough silence, consider it the end of speech
              if (this.speechDetected) {
                this.speechDetected = false;
                
                // If we detect the end of speech and we have audio data, send it
                if (wasSpeaking && this.audioChunks.length > 0) {
                  this.log('Speech ended, sending audio');
                  this.pauseRecording().then(() => {
                    this.sendAudio();
                  });
                  return; // Stop monitoring for now
                }
              }
            }
          }
          
          // If speech started, reset silence timer
          if (this.speechDetected && !wasSpeaking) {
            this.log('Speech detected');
            this.silenceStartTime = null;
            
            // If we were going to send audio due to silence, cancel that
            if (this.silenceDetectionTimer) {
              clearTimeout(this.silenceDetectionTimer);
              this.silenceDetectionTimer = null;
            }
          }
        }
        
        // Continue monitoring if still connected
        if (this.isConnected) {
          requestAnimationFrame(checkAudio);
        }
      };
      
      checkAudio();
    }
  
    /**
     * Start recording audio
     */
    async startRecording() {
      if (!this.isConnected) {
        this.onErrorCallback(new Error('Not connected to server'));
        return false;
      }
      
      if (this.isRecording) {
        return true; // Already recording
      }
      
      try {
        // Initialize audio context for speech detection
        const audioContextInitialized = await this.initAudioContext();
        if (!audioContextInitialized) {
          throw new Error('Could not initialize audio context');
        }
        
        // Reset speech detection state
        this.speechDetected = false;
        this.consecutiveSpeechFrames = 0;
        this.consecutiveSilenceFrames = 0;
        
        this.mediaRecorder = new MediaRecorder(this.audioStream, {
          mimeType: 'audio/webm;codecs=opus'
        });
        
        this.audioChunks = [];
        
        this.mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            this.audioChunks.push(event.data);
          }
        };
        
        // Start recording
        this.mediaRecorder.start(100); // Collect data in small chunks
        this.isRecording = true;
        this.updateStatus('recording');
        this.log('Recording started');
        
        return true;
      } catch (error) {
        this.log('Error starting recording:', error);
        this.onErrorCallback(error);
        return false;
      }
    }
  
    /**
     * Pause recording without sending audio yet
     */
    async pauseRecording() {
      if (this.mediaRecorder && this.isRecording && this.mediaRecorder.state === 'recording') {
        // Stop the media recorder but keep the chunks
        this.mediaRecorder.stop();
        this.isRecording = false;
        this.updateStatus('paused');
        
        // Need to wait for the ondataavailable event to fire
        return new Promise(resolve => {
          setTimeout(() => {
            this.log('Recording paused');
            resolve();
          }, 100);
        });
      }
      return Promise.resolve();
    }
  
    /**
     * Send the collected audio to the server
     */
    async sendAudio() {
      if (this.audioChunks.length === 0) {
        this.log('No audio to send');
        return;
      }
      
      try {
        this.updateStatus('processing');
        
        // Create a blob from the audio chunks
        const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm;codecs=opus' });
        this.lastSentAudioSize = audioBlob.size;
        this.audioChunks = []; // Clear for next recording
        
        // Convert blob to array buffer
        const arrayBuffer = await audioBlob.arrayBuffer();
        
        // Send the audio data if the connection is open
        if (this.isConnected && this.websocket && this.websocket.readyState === WebSocket.OPEN) {
          this.websocket.send(arrayBuffer);
          this.log('Audio sent to server, size:', arrayBuffer.byteLength);
          
          // Signal to the server that speech has ended
          this.sendCommand('end_speech');
        } else {
          this.log('WebSocket not ready, cannot send audio');
        }
      } catch (error) {
        this.log('Error sending audio:', error);
        this.onErrorCallback(error);
      }
    }
  
    /**
     * Send start_interview message with userId (and optional formId)
     * This must be called after WebSocket connection to initialize or resume the interview
     */
    sendStartInterview(userId, formId = null) {
      if (!this.isConnected || !this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
        this.log('Cannot send start_interview, not connected');
        return false;
      }
      
      try {
        const payload = {
          type: 'start_interview',
          userId: userId,
        };
        if (formId) {
          payload.formId = formId;
        }
        const message = JSON.stringify(payload);
        
        this.websocket.send(message);
        this.log('start_interview sent with userId:', userId, 'formId:', formId);
        return true;
      } catch (error) {
        this.log('Error sending start_interview:', error);
        if (this.onErrorCallback) {
          this.onErrorCallback(error);
        }
        return false;
      }
    }
    
    /**
     * Set userId (useful if userId becomes available after client creation)
     */
    setUserId(userId) {
      this.userId = userId;
      // If already connected, send start_interview with new userId
      if (this.isConnected && userId) {
        this.sendStartInterview(userId, this.formId || null);
      }
    }
    
    /**
     * Send a command to the server
     */
    sendCommand(command, data = {}) {
      if (!this.isConnected || !this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
        this.log('Cannot send command, not connected');
        return false;
      }
      
      try {
        const message = JSON.stringify({
          type: 'command',
          command: command,
          ...data
        });
        
        this.websocket.send(message);
        this.log('Command sent:', command);
        return true;
      } catch (error) {
        this.log('Error sending command:', error);
        if (this.onErrorCallback) {
          this.onErrorCallback(error);
        }
        return false;
      }
    }
  
    /**
     * Stop recording and clean up
     */
    stopRecording() {
      if (this.mediaRecorder && this.isRecording && this.mediaRecorder.state === 'recording') {
        this.mediaRecorder.stop();
        this.isRecording = false;
        this.updateStatus('stopped');
        this.log('Recording stopped');
      }
      
      // Clear any pending silence timers
      if (this.silenceDetectionTimer) {
        clearTimeout(this.silenceDetectionTimer);
        this.silenceDetectionTimer = null;
      }
    }
  
    /**
     * Get the current interview status
     */
    async getInterviewStatus() {
      try {
        const response = await fetch(`${this.serverUrl.replace('wss://', 'https://').replace('ws://', 'http:')}/interview_status/${this.clientId}`);
        if (!response.ok) {
          throw new Error(`Failed to get interview status: ${response.statusText}`);
        }
        return await response.json();
      } catch (error) {
        this.log('Error getting interview status:', error);
        this.onErrorCallback(error);
        return null;
      }
    }
  
    /**
     * Get the current interview data
     */
    async getInterviewData() {
      try {
        const response = await fetch(`${this.serverUrl.replace('wss://', 'https://').replace('ws://', 'http:')}/interview_data/${this.clientId}`);
        if (!response.ok) {
          throw new Error(`Failed to get interview data: ${response.statusText}`);
        }
        return await response.json();
      } catch (error) {
        this.log('Error getting interview data:', error);
        this.onErrorCallback(error);
        return null;
      }
    }
  
    /**
     * Save the current interview data
     */
    async saveInterview() {
      try {
        const response = await fetch(`${this.serverUrl.replace('wss://', 'https://').replace('ws://', 'http:')}/save_interview/${this.clientId}`, {
          method: 'POST'
        });
        if (!response.ok) {
          throw new Error(`Failed to save interview: ${response.statusText}`);
        }
        return await response.json();
      } catch (error) {
        this.log('Error saving interview:', error);
        this.onErrorCallback(error);
        return null;
      }
    }
  }