class ChannelMicrophone extends AudioWorkletProcessor {
    constructor(){super();this.frame=new Int16Array(960);this.used=0}
    process(inputs){
        const samples=inputs[0][0];
        if(samples)for(const sample of samples){
            this.frame[this.used++]=Math.round(Math.max(-1,Math.min(1,sample))*32767);
            if(this.used===this.frame.length){
                this.port.postMessage(this.frame.buffer,[this.frame.buffer]);
                this.frame=new Int16Array(960);this.used=0
            }
        }
        return true
    }
}
registerProcessor('channel-microphone',ChannelMicrophone);
