[object Object]

## DSP cache

Analysis is cached in `audio_analysis_cache` by audio SHA-256 and analysis
version. Reusing identical audio therefore avoids repeating the expensive DSP
pass while each output still receives its own analysis record.
