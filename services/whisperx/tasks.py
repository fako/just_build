from enum import Enum
from pathlib import Path
from invoke import task, Collection, Context, Exit


class ModelSize(Enum):
    LARGE = "large"
    MEDIUM = "medium"
    SMALL = "small"
    TINY = "tiny"


@task(
    help={
        "wav_file": "Path to the audio file you want to transcribe.",
        "application": "Application to transcribe the file for.",
        "size": "Size of the model to use (defaults to large)",
        "output_directory": "Path to the directory to store the transcription.",
        "language": "Language to expect during transcription.",
        "speaker_count": "Indicate how may speakers are in the conversation."
    }
)
def transcribe_audio(ctx: Context, wav_file: str, application: str, size: str | None = None, output_directory: str | None = None,
                     language: str | None = None, speaker_count: int | None = None) -> None:
    """
    Transcribe an audio file to a JSON output format
    """
    # Progress inputs
    service_directory = Path("services", "whisperx")
    audio = service_directory / "sources" / application / wav_file
    if not audio.exists():
        raise Exit(f"Can't find audio file: {audio.name}")
    output_directory = service_directory / "outputs" / application
    if not output_directory.exists():
        raise Exit(f"Output directory does not exist: {output_directory.name}")
    if not output_directory.is_dir():
        raise Exit(f"Given output directory is not a directory: {output_directory.name}")
    size = ModelSize(size) if size else ModelSize.LARGE
    language = language or "nl"
    speaker_count = speaker_count or 2

    # Prepare and run command
    command = (
        f"whisperx {audio} "
        f"--model {size.value} --language {language} --device cuda --compute_type=float16 --vad_method=silero "
        f"--diarize --max_speakers {speaker_count} --min_speakers {speaker_count} "
        f"--output_format json --output_dir {output_directory} "
        f"--hf_token {ctx.config.resources.hugging_face.access_token}"
    )
    ctx.run(command, echo=True, pty=True)


collection = Collection(
    "whisperx",
    transcribe_audio
)
