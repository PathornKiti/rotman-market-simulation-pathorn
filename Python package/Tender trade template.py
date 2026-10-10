#!/usr/bin/env python
# coding: utf-8

# # Tender trade v3

# In[ ]:


import signal
import requests
import numpy
import pandas as pd
from time import sleep
from collections import defaultdict


# In[ ]:


# this class definition allows us to print error messages and stop the program when needed
class ApiException(Exception):
    pass


# Available URL endpoints for making request:
# https://rit.306w.ca/RIT-REST-API/1.0.3/#/

# In[ ]:


# this helper method returns all our securities position
def get_securities_position(session):
    resp = session.get('http://localhost:9999/v1/securities')
    if resp.ok:
        securities = resp.json()
        positions = {}
        for security in securities:
            positions[security['ticker']] = security['position']
        return positions
    raise ApiException('Authorization error. Please check API key.')


# In[ ]:


# this helper method returns the current 'tick' of the running case
def get_tick(session):
    resp = session.get('http://localhost:9999/v1/case')
    if resp.ok:
        case = resp.json()
        return case['tick']
    raise ApiException('Authorization error. Please check API key.')


# In[ ]:


# this helper method returns the number of total ticks in each period
def get_total_ticks(session):
    resp = session.get('http://localhost:9999/v1/case')
    if resp.ok:
        case = resp.json()
        return case['ticks_per_period']
    raise ApiException('Authorization error. Please check API key.')


# check_tender returns the information about the soonest to expire tender offer at the following format:
# 
# [
#   {
#     'tender_id': 0,
#     'period': 0,
#     'tick': 0,
#     'expires': 0,
#     'caption': 'string',
#     'quantity': 0,
#     'action': 'BUY',
#     'is_fixed_bid': true,
#     'price': 0
#   }
# ]

# In[ ]:


# this helper method check if there is any active tender offer and return the one that will expire first.
def check_tender(session):
    resp = session.get('http://localhost:9999/v1/tenders')
    if resp.ok:
        tender = resp.json()
        if len(tender) != 0:
            soonest_tender = min(tender, key=lambda x: x['expires'])
            return soonest_tender
        else: return tender
    raise ApiException('Authorization error. Please check API key.')


# order_book returns the information about current limit order book in a simplified format:
# 
# $$\{    'bids'  : [ \{'price':10.00, \quad 'quantity':2500\}, \{'price': 9.95, \quad'quantity':2000\} ], $$
# 
# $$'asks' : [ \{'price':10.03, \quad 'quantity':2500\}, \{'price': 10.05,\quad 'quantity':5500\} ] \}$$

# In[ ]:


# this helper method return the updated order book that aggregates all the quantity of orders at the same price.
def order_book(session, ticker):
    payload = {'ticker':ticker}
    resp = session.get('http://localhost:9999/v1/securities/book', params = payload)
    if resp.ok:
        book = resp.json()
        agg_book = {}
        dd = defaultdict(int)
        
        for bid in book['bids']:
            dd[bid['price']] += (bid['quantity'] - bid['quantity_filled'])
            
        agg_book['bids'] = [{'price':x, 'quantity':y} for x, y in dd.items()]
        
        for ask in book['asks']:
            dd[ask['price']] += (ask['quantity'] - ask['quantity_filled'])
            
        agg_book['asks'] = [{'price':x, 'quantity':y} for x, y in dd.items()]
        
        return agg_book
    raise ApiException('Authorization error. Please check API key.')


# ### Basic setup for connecting with server
# 1. Create a Session object to manage connections and requests to the RIT client.
# 2. Add the API key to the Session to authenticate with every request.
# 3. Make a request to the appropriate URL endpoint, usually using the get() or post() method.
#     In general, the base URL is http://localhost:9999/v1/ followed by a method name and potentially some parameters.
# 4. Check that the response is as expected.
# 5. Parse the returned data (if applicable) by calling the json() method.
# 6. Do something with the parsed data

# In[ ]:


def main():
    with requests.Session() as s:
        
        #parameter that can be adjusted
        API_KEY = {'X-API-key': 'LEB3SN6O'}
        s.headers.update(API_KEY)      
        
        #initial setup time activating algorithm
        start_time = 10
        end_time = get_total_ticks(s) - 10
        sleep_time = 1

        # get current tick and wait to start 
        tick = get_tick(s)
        while tick <= start_time:
            sleep(sleep_time)
            tick = get_tick(s)
        
        # during active time
        while tick > start_time and tick < end_time:
            
            # get information of book for current tick
            print('----- Current tick is {} -----'.format(tick))
            book = order_book(s,'CRZY')
            #print(book)
            
            tender = check_tender(s)
            if len(tender) != 0:  
                print('Tender: Ticker = {}, action = {}, quantity = {}, price = {}'
                      .format(tender['ticker'], tender['action'], tender['quantity'], tender['price']))
                if tick < end_time/2:
                    s.delete('http://localhost:9999/v1/tenders/' + str(tender['tender_id']))
                    print('Tender offer declined')
                else: 
                    s.post('http://localhost:9999/v1/tenders/' + str(tender['tender_id']))
                    print('Tender offer accepted')
                        
                        
            # trade away (reduce position) whenever a non-zero position detected
            positions = get_securities_position(s)
            for ticker in positions:
                if positions[ticker] > 0:
                    # sell at most 3000 shares per second until the positive position is gone
                    resp = s.post('http://localhost:9999/v1/orders', params={'ticker':ticker, 'type':'MARKET', 
                                        'quantity': min(positions[ticker],3000), 'action':'SELL'})
                elif positions[ticker] < 0:
                    # buy at most 3000 shares per second until the negative position is gone
                    resp = s.post('http://localhost:9999/v1/orders', params={'ticker':ticker, 'type':'MARKET', 
                                        'quantity': min(abs(positions[ticker]),3000), 'action':'BUY'})
                
            sleep(sleep_time)
            tick = get_tick(s)


# In[ ]:


if __name__ == '__main__':
    main()

