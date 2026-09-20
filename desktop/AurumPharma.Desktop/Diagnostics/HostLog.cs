using System.Diagnostics;

namespace AurumPharma_Desktop.Diagnostics;

internal static class HostLog
{
    private const long MaxLogBytes = 1_048_576;
    private static readonly object Sync = new();
    private static readonly string LogDirectory = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Aurum Pharma",
        "Logs");
    private static readonly string LogPath = Path.Combine(LogDirectory, "host.log");

    public static void Information(string eventName) => Write("Information", eventName);

    public static void Warning(string eventName) => Write("Warning", eventName);

    public static void Error(string eventName, Exception exception) =>
        Write("Error", $"{eventName}; exception={exception.GetType().Name}");

    private static void Write(string level, string eventName)
    {
        var entry = $"{DateTimeOffset.UtcNow:O} [{level}] {eventName}";
        Trace.WriteLine(entry);

        try
        {
            lock (Sync)
            {
                Directory.CreateDirectory(LogDirectory);
                if (File.Exists(LogPath) && new FileInfo(LogPath).Length >= MaxLogBytes)
                {
                    File.Move(LogPath, Path.Combine(LogDirectory, "host.previous.log"), true);
                }

                File.AppendAllText(LogPath, entry + Environment.NewLine);
            }
        }
        catch
        {
            // Diagnostics must never prevent the pharmacy shell from starting.
        }
    }
}
