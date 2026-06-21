using System;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

class Program
{
    private static readonly string ApiBase = "https://www.clubhouseapi.com/api";

    static async Task<int> Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.WriteLine(JsonSerializer.Serialize(new { success = false, error = "Usage: ch-auth-helper <start|complete> <phone_number> [verification_code] [device_id]" }));
            return 1;
        }

        string action = args[0].ToLower();
        string phoneNumber = args[1];
        string verificationCode = args.Length > 2 ? args[2] : "";
        string deviceId = args.Length > 3 && !string.IsNullOrEmpty(args[3]) ? args[3] : Guid.NewGuid().ToString().ToUpper();

        using var client = new HttpClient();
        client.DefaultRequestHeaders.Add("CH-Languages", "en-US");
        client.DefaultRequestHeaders.Add("CH-Locale", "en_US");
        client.DefaultRequestHeaders.Add("CH-AppBuild", "3389");
        client.DefaultRequestHeaders.Add("CH-AppVersion", "1.0.1");
        client.DefaultRequestHeaders.Add("CH-DeviceId", deviceId);
        client.DefaultRequestHeaders.TryAddWithoutValidation("User-Agent", "clubhouse/android/3389");

        try
        {
            if (action == "start")
            {
                var payload = new { phone_number = phoneNumber };
                var json = JsonSerializer.Serialize(payload);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                var response = await client.PostAsync($"{ApiBase}/start_phone_number_auth", content);
                var responseBody = await response.Content.ReadAsStringAsync();

                if (!response.IsSuccessStatusCode)
                {
                    Console.WriteLine(JsonSerializer.Serialize(new { success = false, error = $"Clubhouse returned status {(int)response.StatusCode}: {responseBody}" }));
                    return 1;
                }

                Console.WriteLine(responseBody);
                return 0;
            }
            else if (action == "complete")
            {
                var payload = new { phone_number = phoneNumber, verification_code = verificationCode };
                var json = JsonSerializer.Serialize(payload);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                var response = await client.PostAsync($"{ApiBase}/complete_phone_number_auth", content);
                var responseBody = await response.Content.ReadAsStringAsync();

                if (!response.IsSuccessStatusCode)
                {
                    Console.WriteLine(JsonSerializer.Serialize(new { success = false, error = $"Clubhouse returned status {(int)response.StatusCode}: {responseBody}" }));
                    return 1;
                }

                Console.WriteLine(responseBody);
                return 0;
            }
            else
            {
                Console.WriteLine(JsonSerializer.Serialize(new { success = false, error = $"Unknown action: {action}" }));
                return 1;
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine(JsonSerializer.Serialize(new { success = false, error = ex.Message }));
            return 1;
        }
    }
}
