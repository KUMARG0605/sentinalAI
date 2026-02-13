use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::sync::{Arc, Mutex};
use vosk::{Model, Recognizer};

fn main() -> anyhow::Result<()> {
    println!("Initializing Vosk Speech Recognition...");

    // 1. Load the Model
    let model = Model::new("./model").expect(" Could not find 'model' folder!");
    
    // 2. Create the Recognizer
    let recognizer = Recognizer::new(&model, 48000.0).expect("Could not create recognizer");
    
    let recognizer = Arc::new(Mutex::new(recognizer));
    let recognizer_clone = recognizer.clone();

    // 3. Setup Mic
    let host = cpal::default_host();
    let device = host.default_input_device().expect("No input device!");
    
    let config = cpal::StreamConfig {
        channels: 1,
        sample_rate: cpal::SampleRate(48000),
        buffer_size: cpal::BufferSize::Default,
    };

    println!("Listening for 'Jarvis'...");

    let stream = device.build_input_stream(
        &config,
        move |data: &[f32], _: &_| {
            let samples: Vec<i16> = data.iter()
                .map(|&sample| (sample * 32767.0) as i16)
                .collect();

            let mut rec = recognizer_clone.lock().unwrap();
            
            let state = rec.accept_waveform(&samples);
            
            if let Ok(vosk::DecodingState::Finalized) = state {
                let result = rec.final_result();
                
                if let Some(single_result) = result.single() {
                    let text = single_result.text;
                    
                    if !text.is_empty() {
                        println!("You said: '{}'", text);
                        
                        if text.to_lowercase().contains(" hey siri") {
                            println!("\n WAKE WORD DETECTED: Hey Siri is listening!\n");
                        }
                    }
                }
            }
            else {
                let partial = rec.partial_result();
                let partial_text = partial.partial;
                
                if partial_text.to_lowercase().contains("jarvis") {
                     rec.reset();
                     println!("\n INSTANT TRIGGER: 'Hey Jarvis' detected!");
                }
            }
        },
        move |err| eprintln!("Error: {}", err),
        None,
    )?;

    stream.play()?;
    std::thread::park();
    Ok(())
}
