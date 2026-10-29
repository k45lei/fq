#!/bin/bash

#to get cloudflare token
#go to profile > API Tokens > Create Token > Edit zone DNS |Use Template| > 
# Permissions: 
# {
# 	Zone | DNS | Edit
# 	Account | Cloudflare Tunnel | Edit
# }


# put your domain name here
DOMAIN=""
# put the port you are hosting on:
PORT=""
# paste your account id here
ACCOUNT_ID=""
# paste your cloudflare api token here
CLOUDFLARE_API_TOKEN=""
# whatever you want to name your tunnel
TUNNEL_NAME=""
# paste your zone id here	
ZONE_ID=""


touch /dev/shm/tunnel

# create the tunnel:
curl "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel" \
	--request POST \
	--header "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
	--json '{
		"name": "$TUNNEL_NAME",
		"config_src": "cloudflare"
	}' > /dev/shm/tunnel


TUNNEL_ID=$(awk -F'"' '/"id":/ {print $4; exit}' /dev/shm/tunnel)
TUNNEL_TOKEN=$(awk -F'"' '$2 == "token" {print $4; exit}' /dev/shm/tunnel)

# add a website to cloudflare
# change domain nameservers to cloudflare


# PUT request to route localhost to the domain:
curl "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" \
	--request PUT \
	--header "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
	--json '{
		"config": {
				"ingress": [
						{
								"hostname": "$DOMAIN",
								"service": "http://localhost:$PORT",
								"originRequest": {}
						},
						{
								"service": "http_status:404" 
						}
				]
		}
	}'

# create a DNS record:
curl "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
	--request POST \
	--header "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
	--json '{
		"type": "CNAME",
		"proxied": true,
		"name": "$DOMAIN",
		"content": "$TUNNEL_ID.cfargotunnel.com"
	}'


# install and run tunnel
sudo cloudflared service install $TUNNEL_TOKEN


# uncomment below if already installed
# cloudflared tunnel run --token $TUNNEL_TOKEN



# sanity check
curl "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel/c1744f8b-faa1-48a4-9e5c-02ac921467fa" \
	--request GET \
	--header "Authorization: Bearer $CLOUDFLARE_API_TOKEN" | grep "success"
