# Weather Insights API

A comprehensive weather API for developers, providing current weather, forecasts, and historical data.

## Features

- ✅ Current weather data for any city
- ✅ 5-day weather forecast
- ✅ Historical weather data
- ✅ Bulk city requests
- ✅ Simple authentication
- ✅ Rate limiting ready
- ✅ JSON responses

## Quick Start

### Get Current Weather
\```bash
curl -X GET "https://your-api.vercel.app/api/weather/current?city=London" \
  -H "X-RapidAPI-Key: your-api-key"
\```

### Get Forecast
\```bash
curl -X GET "https://your-api.vercel.app/api/weather/forecast?city=NewYork&days=5" \
  -H "X-RapidAPI-Key: your-api-key"
\```

## Deployment

1. Clone this repository
2. Install dependencies: `pip install -r requirements.txt`
3. Set environment variables
4. Deploy to Vercel: `vercel --prod`

## RapidAPI Integration

1. Sign up at [RapidAPI](https://rapidapi.com)
2. Click "Add New API"
3. Enter your deployed API URL
4. Configure pricing plans (Free tier recommended)
5. Publish your API

## Pricing Tiers

- **Free**: 100 requests/day
- **Basic**: $9.99/month - 10,000 requests
- **Pro**: $49.99/month - 50,000 requests
- **Enterprise**: Custom pricing

## Support

Contact: support@yourapi.com
Documentation: https://docs.yourapi.com